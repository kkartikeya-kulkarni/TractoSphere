"""Read-only indexed TCK/TRK access without materializing a whole tractogram.

NiBabel supplies the authoritative TrackVis-to-RAS affine and named metadata
layout. Indexing validates records in a worker. Only requested fibers are copied
from the memory map; source files are never rewritten or cached as duplicate MRI.
"""

import struct
from array import array
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from threading import RLock

import nibabel as nib
import numpy as np
from nibabel.streamlines.trk import decode_value_from_name, get_affine_trackvis_to_rasmm


class IndexedStreamlines(Sequence):
    """Random-access immutable physical-coordinate fibers, with source attributes."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        stat = self.path.stat()
        self.signature = (stat.st_size, stat.st_mtime_ns)
        self.source = nib.streamlines.load(self.path, lazy_load=True)
        self.header = self.source.header
        self.trk = self.path.suffix.lower() == ".trk"
        self.dtype = np.dtype(self.header["endianness"] + "f4")
        self.offsets = array("q")
        self.counts = array("q")
        self.lengths = array("d")
        self.lower = np.full(3, np.inf)
        self.upper = np.full(3, -np.inf)
        self.scalar_count = int(self.header.get("nb_scalars_per_point", 0))
        self.property_count = int(self.header.get("nb_properties_per_streamline", 0))
        self.affine = get_affine_trackvis_to_rasmm(self.header) if self.trk else np.eye(4)
        self._lock = RLock()
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self.raw = np.memmap(self.path, mode="r", dtype=np.uint8)
        if self.trk:
            self._index_trk()
        else:
            self._index_tck()
        self.lengths = np.asarray(self.lengths)
        self.verify_source()

    def verify_source(self):
        stat = self.path.stat()
        if (stat.st_size, stat.st_mtime_ns) != self.signature:
            raise ValueError("Tractography source changed after indexing; reopen the file")

    def _index_trk(self):
        offset = int(self.header["_offset_data"])
        while offset < len(self.raw):
            if offset + 4 > len(self.raw):
                raise ValueError("Truncated TRK point count")
            count = struct.unpack_from(self.header["endianness"] + "i", self.raw, offset)[0]
            if count < 2:
                raise ValueError("Invalid TRK streamline point count")
            end = offset + 4 + 4 * (count * (3 + self.scalar_count) + self.property_count)
            if end > len(self.raw):
                raise ValueError("Truncated TRK streamline")
            self.offsets.append(offset + 4)
            self.counts.append(count)
            line = self[len(self.offsets) - 1]
            if not np.isfinite(line).all():
                raise ValueError("Nonfinite TRK coordinates")
            self.lower = np.minimum(self.lower, line.min(axis=0))
            self.upper = np.maximum(self.upper, line.max(axis=0))
            self.lengths.append(float(np.linalg.norm(np.diff(line, axis=0), axis=1).sum()))
            offset = end
        expected = int(self.header["nb_streamlines"])
        if expected and expected != len(self):
            raise ValueError("TRK header count differs from complete records")

    def _index_tck(self):
        dtype = np.dtype(self.header["_dtype"])
        offset = int(self.header["_offset_data"])
        if (len(self.raw) - offset) % (3 * dtype.itemsize):
            raise ValueError("Truncated TCK coordinate data")
        points = np.ndarray(
            ((len(self.raw) - offset) // (3 * dtype.itemsize), 3),
            dtype=dtype,
            buffer=self.raw,
            offset=offset,
        )
        self.dtype = dtype
        start = 0
        cumulative = 0.0
        previous_total = 0.0
        previous_point = None
        terminated = False
        for begin in range(0, len(points), 262144):
            block = points[begin : begin + 262144]
            finite = np.isfinite(block).all(axis=1)
            separators = np.isnan(block).all(axis=1)
            terminal = np.isposinf(block).all(axis=1)
            if not (finite | separators | terminal).all():
                raise ValueError("Invalid TCK separator or coordinate")
            if finite.any():
                self.lower = np.minimum(self.lower, block[finite].min(axis=0))
                self.upper = np.maximum(self.upper, block[finite].max(axis=0))
            distance = np.zeros(len(block))
            valid_pair = finite[1:] & finite[:-1]
            distance[1:][valid_pair] = np.linalg.norm(
                block[1:][valid_pair] - block[:-1][valid_pair], axis=1
            )
            if previous_point is not None and finite[0] and np.isfinite(previous_point).all():
                distance[0] = np.linalg.norm(block[0] - previous_point)
            prefix = np.cumsum(distance) + cumulative
            for local in np.flatnonzero(separators):
                end = begin + int(local)
                if end - start < 2:
                    raise ValueError("Invalid empty/one-point TCK streamline")
                self.offsets.append(offset + start * 3 * dtype.itemsize)
                self.counts.append(end - start)
                self.lengths.append(float(prefix[local] - previous_total))
                previous_total = float(prefix[local])
                start = end + 1
            if terminal.any():
                terminal_indices = np.flatnonzero(terminal)
                if len(terminal_indices) != 1 or begin + terminal_indices[0] != len(points) - 1:
                    raise ValueError("TCK end marker is not the last record")
                if start != len(points) - 1:
                    raise ValueError("TCK final streamline lacks separator")
                terminated = True
            cumulative = float(prefix[-1])
            previous_point = block[-1]
        if not terminated:
            raise ValueError("TCK end marker missing")
        expected = int(self.header.get("count", 0))
        if expected and expected != len(self):
            raise ValueError("TCK header count differs from complete records")

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, index):
        with self._lock:
            return self._read(index)

    def _read(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        width = 3 + self.scalar_count if self.trk else 3
        values = np.ndarray(
            (self.counts[index], width),
            dtype=self.dtype,
            buffer=self.raw,
            offset=self.offsets[index],
        )
        points = values[:, :3]
        if self.trk:
            result = np.asarray(nib.affines.apply_affine(self.affine, points), dtype=np.float32)
        else:
            result = np.asarray(points, dtype=np.float32)
        result.setflags(write=False)
        while self._cache and self._cache_bytes + result.nbytes > 32 * 1024**2:
            _, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.nbytes
        if result.nbytes <= 32 * 1024**2:
            self._cache[index] = result
            self._cache_bytes += result.nbytes
        return result

    def item(self, index):
        """Read original named TRK scalars/properties through NiBabel's layout."""
        from nibabel.streamlines.tractogram import TractogramItem

        dpp, dps = {}, {}
        if self.trk:
            values = np.ndarray(
                (self.counts[index], 3 + self.scalar_count),
                dtype=self.dtype,
                buffer=self.raw,
                offset=self.offsets[index],
            )
            cursor = 3
            for raw_name in self.header["scalar_name"]:
                name, width = decode_value_from_name(raw_name)
                if width:
                    dpp[name] = values[:, cursor : cursor + width].copy()
                    cursor += width
            if cursor < 3 + self.scalar_count:
                dpp["scalars"] = values[:, cursor:].copy()
            prop_offset = self.offsets[index] + self.counts[index] * (3 + self.scalar_count) * 4
            properties = np.ndarray(
                (self.property_count,), dtype=self.dtype, buffer=self.raw, offset=prop_offset
            )
            cursor = 0
            for raw_name in self.header["property_name"]:
                name, width = decode_value_from_name(raw_name)
                if width:
                    dps[name] = properties[cursor : cursor + width].copy()
                    cursor += width
            if cursor < self.property_count:
                dps["properties"] = properties[cursor:].copy()
        return TractogramItem(self[index], dps, dpp)
