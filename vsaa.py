"""
VSAA v2.0 - Very Simple Random Access Archive
Reference Python implementation
Defines:
```
Classes:
class VSAAArchive()
```
```
Errors:
class VSAAError(Exception)
class VSAAValidityError(ValueError, VSAAError)
class VSAAPathError(VSAAValidityError, ValueError, VSAAError)
class VSAAChecksumError(VSAAValidityError, VSAAError)
class VSAAVersionMismatchWarning(Warning)
class VSAAMajorVersionMismatchError(VSAAValidityError, VSAAError)
```
```
Functions:
def create_archive(archive_path, files, compression = CompressionMode.NONE) -> None
```
```
Other:
class CompressionMode(Enum)
CompressionMode.NONE
CompressionMode.DEFLATE
StrPath = str
```
"""

import os as _os
import struct as _st
from typing import Iterator as _Iterator, final as _final
import zlib as _zlib
import binascii as _binascii
from enum import Enum as _Enum

# ==============================
# Constants
# ==============================

_MAGIC = b"VSAA"
_FOOTER_MAGIC = b"VEND"
_DEFAULT_ZLIB_WBITS = -15 #this number is chosen specifically for interop
DEFAULT_MAX_READ_SIZE = 40 * 1024**3 #this is 40GiB
MAJ_VERSION = 2 #last major update: changed to use raw DEFLATE
MIN_VERSION = 0

@_final
class CompressionMode(_Enum):
    NONE = 0
    DEFLATE = 1

StrPath = str

# ==============================
# Exceptions
# ==============================

class VSAAError(Exception):
    """VSAA Error base class for all other VSAA Errors."""
    pass

class VSAAValidityError(ValueError, VSAAError):
    """VSAA Error for invalid parameters/archive layouts."""
    pass

class VSAAPathError(VSAAValidityError, ValueError, VSAAError):
    """VSAA Error for invalid paths."""
    pass

class VSAAChecksumError(VSAAValidityError, VSAAError):
    """VSAA Error for invalid checksums."""
    pass

class VSAAVersionMismatchWarning(Warning):
    """VSAA Warning when reading an archive with a different minor version tag than the reader's."""
    pass

class VSAAMajorVersionMismatchError(VSAAValidityError, VSAAError):
    """VSAA Warning when reading an archive with a different major version tag than the reader's."""
    pass

class VSAAOverflowError(VSAAValidityError, VSAAError):
    """VSAA Error for overflows."""
    pass

# ==============================
# Helpers
# ==============================

def _validate_path(path: StrPath):
    segments = path.split("/")
    if path.startswith("/"):
        raise VSAAPathError(f"Paths must be relative: {path}")
    if ".." in segments:
        raise VSAAPathError(f"Path may not contain '..': {path}")
    if "" in segments:
        raise VSAAPathError(f"Path contains empty segment {path}")
    if "." in segments:
        raise VSAAPathError(f"Path may not contain '.': {path}")
    return path

class _CompressorWrapper:
    def __init__(self, compression_mode: CompressionMode = CompressionMode.NONE, level: int = 6):
        self.mode = compression_mode
        if self.mode == CompressionMode.DEFLATE:
            self._compressor = _zlib.compressobj(level, wbits=_DEFAULT_ZLIB_WBITS)
        elif self.mode == CompressionMode.NONE:
            self._compressor = None
        else:
            raise VSAAValidityError(f"Unsupported compression: compression type {self.mode}")

    def compress(self, uncompressed: bytes):
        if not self._compressor:
            return uncompressed
        else:
            return self._compressor.compress(uncompressed)
    
    def flush(self):
        if not self._compressor:
            return b''
        else:
            return self._compressor.flush()

# ==============================
# Writer
# ==============================

def create_archive(archive_path: StrPath, *dirs_or_files: StrPath, compression: CompressionMode = CompressionMode.NONE, level: int = 6) -> None:
    def file_iter():
        def _recurse_dir_walk(base_dir: StrPath, cur_dir: StrPath) -> _Iterator[tuple[str, str]]:
            for entry in sorted(_os.listdir(cur_dir)):
                fs_path = _os.path.join(cur_dir, entry)
                rel_path = _os.path.relpath(fs_path, base_dir).replace("\\", "/")

                if _os.path.isdir(fs_path):
                    yield from _recurse_dir_walk(base_dir, fs_path)
                elif _os.path.isfile(fs_path):
                    yield (rel_path, fs_path)
        for df in dirs_or_files:
            if _os.path.isdir(df):
                yield from _recurse_dir_walk(df, df)
            elif _os.path.isfile(df):
                arc_path = _os.path.basename(df)
                yield (arc_path, df)
            else:
                raise VSAAPathError(f"Not a file or directory: {df}")

    index: list[tuple[bytes, int, int, int, int, CompressionMode]] = []

    with open(archive_path, "wb") as out:

        # ---- Header ----
        out.write(_MAGIC)
        out.write(bytes([MAJ_VERSION, MIN_VERSION]))
        out.write(b"\x00" * 10)

        # ---- Data region ----
        for arc_path, fs_path in file_iter():
            _validate_path(arc_path)
            compressor = _CompressorWrapper(compression, level)
                
            with open(fs_path, "rb") as f:
                offset = out.tell()
                crc = 0
                uncompressed_size = 0
                compressed_size = 0
                while chunk := f.read(65536):
                    crc = _binascii.crc32(chunk, crc)
                    uncompressed_size += len(chunk)
                    compressed_chunk = compressor.compress(chunk)
                    
                    compressed_size += len(compressed_chunk)
                    out.write(compressed_chunk)
            final = compressor.flush()
            compressed_size += len(final)
            out.write(final)

            index.append((
                arc_path.encode("utf-8"),
                offset,
                compressed_size,
                uncompressed_size,
                crc & 0xffffffff,
                compression
            ))

        # ---- Index ----
        index_offset = out.tell()
        out.write(_st.pack("<I", len(index)))

        for path_bytes, off, csize, usize, crc, comp in index:
            if len(path_bytes) > 0xffff:
                raise VSAAOverflowError(f"Paths may not be more than 65535 bytes long: {path_bytes.decode()}")
            out.write(_st.pack("<H", len(path_bytes)))
            out.write(path_bytes)
            out.write(_st.pack("<QQQI B", off, csize, usize, crc, comp.value))

        index_size = out.tell() - index_offset

        # ---- Footer ----
        out.write(_FOOTER_MAGIC)
        out.write(_st.pack("<QI", index_offset, index_size))

# ==============================
# Reader
# ==============================

class VSAAArchive:
    """Represents a VSAA archive"""

    def __init__(self, path: StrPath):
        self.path = path
        self.index: dict[StrPath, dict[str, int]] = {}
        self._file = open(self.path, "rb")
        self._load_index()
    
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
    
    def __repr__(self):
        return f"<VSAAArchive path={self.path!r} files={len(self.index)}>"

    def close(self):
        if not self._file.closed: self._file.close()

    def _load_index(self):
        # Validate header
        magic = self._file.read(4)
        if magic != _MAGIC:
            raise VSAAValidityError("Invalid VSAA archive header")
        
        version = self._file.read(2)
        maj_version = version[0]
        min_version = version[1]
        if maj_version != MAJ_VERSION:
            raise VSAAMajorVersionMismatchError(f"Unsupported VSAA version: reader is v{MAJ_VERSION}.{MIN_VERSION} while archive is v{maj_version}.{min_version}")
        if min_version > MIN_VERSION:
            import warnings
            warnings.warn(f"Unsupported minor changes may be possible: reader is v{MAJ_VERSION}.{MIN_VERSION} while archive is v{maj_version}.{min_version}", VSAAVersionMismatchWarning)
        
        reserved = self._file.read(10)
        if reserved != b"\x00" * 10:
            raise VSAAValidityError("Reserved bytes not zero")

        self._file.seek(-16, _os.SEEK_END)

        footer_magic, index_offset, index_size = _st.unpack("<4sQI", self._file.read(16))

        if footer_magic != _FOOTER_MAGIC:
            raise VSAAValidityError("Invalid VSAA footer")
        
        self._file.seek(0, _os.SEEK_END)
        file_size = self._file.tell()


        if file_size - 16 - index_offset != index_size:
            raise VSAAValidityError("Index size mismatch")

        if index_offset + index_size + 16 > file_size:
            raise VSAAValidityError("Corrupt index region")

        self._file.seek(index_offset)
        count = _st.unpack("<I", self._file.read(4))[0]
        minimum_entry_size = 2 + 29  # path_len + fixed fields
        if count * minimum_entry_size > index_size:
            raise VSAAValidityError("Index entry count exceeds index size")


        for _ in range(count):
            path_len = _st.unpack("<H", self._file.read(2))[0]
            current = self._file.tell()
            if current + path_len > index_offset + index_size:
                raise VSAAValidityError("Path exceeds index bounds")


            path = self._file.read(path_len).decode("utf-8")
            _validate_path(path)

            off, csize, usize, crc, comp = _st.unpack("<QQQI B", self._file.read(29))

            if off + csize > index_offset:
                raise VSAAValidityError("File entry exceeds data region")
            
            if self.exists(path):
                raise VSAAValidityError(f"Duplicate path in index: {path}")

            self.index[path] = {
                "offset": off,
                "csize": csize,
                "usize": usize,
                "crc": crc,
                "comp": comp
            }
        entries = sorted(self.index.values(), key=lambda e: e["offset"])
        for i in range(len(entries)-1):
            if entries[i]["offset"] + entries[i]["csize"] > entries[i+1]["offset"]:
                raise VSAAValidityError("Overlapping file regions")


    def list(self) -> list[StrPath]:
        return list(self.index.keys())


    def exists(self, path: StrPath):
        return path in self.index

    def info(self, path):
        return dict(self.index[path])

    def read(self, path: StrPath, *, max_size: int = DEFAULT_MAX_READ_SIZE):
        if path not in self.index:
            raise VSAAError(f"File not found in archive: {path}")

        entry = self.index[path]

        if entry["usize"] > max_size and max_size > 0:
            raise VSAAOverflowError(f"Too large of an entry for limit of {max_size} bytes")

        self._file.seek(entry["offset"])
        data = self._file.read(entry["csize"])

        try:
            comp = CompressionMode(entry["comp"])
        except ValueError:
            raise VSAAValidityError(f"Unsupported compression type {entry['comp']}")


        if comp == CompressionMode.DEFLATE:
            try:
                data = _zlib.decompress(data, wbits=_DEFAULT_ZLIB_WBITS)
            except _zlib.error as e:
                raise VSAAValidityError("DEFLATE decpmression failed") from e
        elif comp == CompressionMode.NONE:
            pass
        else:
            raise VSAAValidityError(f"Unsupported compression: compression type {comp}")

        if (_binascii.crc32(data) & 0xffffffff) != entry["crc"]:
            raise VSAAChecksumError("CRC mismatch")
        
        
        if len(data) != entry["usize"]:
            raise VSAAValidityError("Size mismatch after decompression")

        return data
    
    def read_stream(self, path: StrPath, chunk_size: int = 65536, *, max_size: int = DEFAULT_MAX_READ_SIZE):
        """
        Stream-decompress a file from the archive.

        Yields decompressed chunks.
        """

        if path not in self.index:
            raise VSAAError(f"File not found in archive: {path}")

        entry = self.index[path]

        self._file.seek(entry["offset"])

        if entry["usize"] > max_size and max_size > 0:
            raise VSAAOverflowError(f"Too large of an entry for limit of {max_size} bytes")

        remaining = entry["csize"]

        try:
            comp = CompressionMode(entry["comp"])
        except ValueError:
            raise VSAAValidityError(f"Unsupported compression type {entry['comp']}")

        # Setup decompressor if needed
        if comp == CompressionMode.DEFLATE:
            decompressor = _zlib.decompressobj(wbits=_DEFAULT_ZLIB_WBITS)
        elif comp == CompressionMode.NONE:
            decompressor = None
        else:
            raise VSAAValidityError(f"Unsupported compression: {comp}")

        crc = 0
        total_out = 0

        while remaining > 0:
            to_read = min(chunk_size, remaining)
            chunk = self._file.read(to_read)

            if len(chunk) != to_read:
                raise VSAAValidityError("Unexpected EOF while reading file data")

            remaining -= len(chunk)

            if decompressor:
                out_chunk = decompressor.decompress(chunk)
            else:
                out_chunk = chunk

            if out_chunk:
                crc = _binascii.crc32(out_chunk, crc)
                total_out += len(out_chunk)
                yield out_chunk

        # Flush decompressor
        if decompressor:
            final = decompressor.flush()
            if decompressor.unused_data:
                raise VSAAValidityError("Trailing data after DEFLATE stream")
            if not decompressor.eof:
                raise VSAAValidityError("Incomplete DEFLATE stream")
            if final:
                crc = _binascii.crc32(final, crc)
                total_out += len(final)
                yield final

        # Final validations
        if crc & 0xffffffff != entry["crc"]:
            raise VSAAChecksumError("CRC mismatch")

        if total_out != entry["usize"]:
            raise VSAAValidityError("Size mismatch after decompression")

    def extract_all(self, out_dir: StrPath):
        _os.makedirs(out_dir, exist_ok=True)

        for path in self.index:
            full_path = _os.path.join(out_dir, path)
            _os.makedirs(_os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "wb") as f:
                for chunk in self.read_stream(path):
                    f.write(chunk)


__all__ = [
    "MAJ_VERSION", "MIN_VERSION", "CompressionMode", "StrPath", "VSAAError",
    "VSAAValidityError", "VSAAPathError", "VSAAChecksumError", "VSAAVersionMismatchWarning",
    "VSAAMajorVersionMismatchError", "create_archive", "VSAAArchive"
    ] 

