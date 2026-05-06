"""
BackupParser: open iTunes/iOS backup (encrypted or unencrypted), resolve paths, read file bytes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple

# Optional: only needed for encrypted backups
try:
    from iphone_backup_decrypt import EncryptedBackup
    from iphone_backup_decrypt import google_iphone_dataprotection
    from iphone_backup_decrypt import utils as _ibd_utils
    _HAS_ENCRYPTED = True
except ImportError:
    EncryptedBackup = None  # type: ignore[misc, assignment]
    google_iphone_dataprotection = None
    _ibd_utils = None
    _HAS_ENCRYPTED = False


def _log():
    from app.logging_config import get_logger
    return get_logger()


def backup_appears_encrypted(backup_path: Path) -> bool:
    """
    Detect iTunes/Finder encrypted backups without opening sqlite.

    Encrypted backups store Manifest.db encrypted on disk; opening it as SQLite yields
    "file is not a database" unless decrypted with the backup password first.

    If Manifest.db on disk already has a SQLite header, we treat the backup as readable
    (e.g. decrypted copy), even when Info.plist still says IsEncrypted.
    """
    manifest_db = backup_path / "Manifest.db"
    if manifest_db.exists() and manifest_db.stat().st_size >= 16:
        try:
            header = manifest_db.read_bytes()[:16]
            if header.startswith(b"SQLite format 3\x00"):
                return False
        except Exception:
            pass

    info = backup_path / "Info.plist"
    if info.exists():
        try:
            import plistlib

            with open(info, "rb") as f:
                plist = plistlib.load(f)
            if plist.get("IsEncrypted") is True:
                return True
        except Exception:
            pass
    m_plist = backup_path / "Manifest.plist"
    if m_plist.exists():
        try:
            import plistlib

            with open(m_plist, "rb") as f:
                plist = plistlib.load(f)
            if plist.get("IsEncrypted") is True:
                return True
        except Exception:
            pass
    # Manifest exists but is not a SQLite header (typical of encrypted-on-disk)
    if manifest_db.exists() and manifest_db.stat().st_size >= 16:
        return True
    return False


def _decrypt_file_no_size_check(encrypted_backup, backup_path: Path, file_id: str, file_bplist: bytes) -> Optional[bytes]:
    """Decrypt one file using keybag + bplist, without asserting decrypted size == plist Size."""
    if not _HAS_ENCRYPTED or google_iphone_dataprotection is None or _ibd_utils is None:
        return None
    try:
        encrypted_backup._read_and_unlock_keybag()
        file_plist = _ibd_utils.FilePlist(file_bplist)
        if file_plist.encryption_key is None:
            return None
        inner_key = encrypted_backup._keybag.unwrapKeyForClass(
            file_plist.protection_class, file_plist.encryption_key
        )
        path = backup_path / file_id[:2] / file_id
        if not path.exists():
            return None
        encrypted_data = path.read_bytes()
        decrypted_data = google_iphone_dataprotection.AESdecryptCBC(encrypted_data, inner_key)
        return google_iphone_dataprotection.removePadding(decrypted_data)
    except Exception:
        return None


class _FileEntry:
    """Opaque entry for get_file_by_path; parser uses file_id (unencrypted) or relative_path (encrypted)."""
    __slots__ = ("file_id", "relative_path", "domain")
    file_id: Optional[str]
    relative_path: Optional[str]
    domain: str

    def __init__(
        self,
        file_id: Optional[str] = None,
        relative_path: Optional[str] = None,
        domain: str = "",
    ):
        self.file_id = file_id
        self.relative_path = relative_path
        self.domain = domain


class _BackupInfo:
    device_name: Optional[str]
    backup_date: Optional[str]

    def __init__(self, device_name: Optional[str] = None, backup_date: Optional[str] = None):
        self.device_name = device_name
        self.backup_date = backup_date or ""


def _read_plist_str(path: Path, *keys: str) -> Optional[str]:
    try:
        import plistlib
        if not path.exists():
            return None
        with open(path, "rb") as f:
            plist = plistlib.load(f)
        for k in keys:
            v = plist.get(k)
            if isinstance(v, str) and v:
                return v
            if v is not None:
                if hasattr(v, "isoformat"):
                    return v.isoformat()
                return str(v)
        return None
    except Exception:
        return None


class BackupParser:
    """
    Open an iTunes/iOS backup and resolve files by domain + relative path.
    Supports unencrypted backups (Manifest.db + hashed files) and encrypted (iphone-backup-decrypt).
    """

    def __init__(
        self,
        backup_path: str,
        passphrase: Optional[str] = None,
        temp_dir: Optional[Path] = None,
    ):
        self.backup_path = Path(backup_path).resolve()
        self.passphrase = passphrase
        self.temp_dir = Path(temp_dir) if temp_dir else (self.backup_path / ".parser_temp")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._encrypted_backup: Any = None
        self._manifest_path: Optional[Path] = None
        self._path_map: dict[tuple[str, str], _FileEntry] = {}  # (domain, relativePath) -> entry
        self._file_id_map: dict[str, _FileEntry] = {}  # fileID -> entry

        if backup_appears_encrypted(self.backup_path) and not passphrase:
            raise ValueError(
                "This backup is encrypted (iTunes/Finder “Encrypt local backup”). "
                "Enter the backup password in the Import dialog and try again. "
                "Without it, Manifest.db cannot be read as a database. "
                "Other programs may have used a saved password or decrypted the backup first."
            )
        if backup_appears_encrypted(self.backup_path) and passphrase and not _HAS_ENCRYPTED:
            raise ValueError(
                "This backup is encrypted, but the decryption library is not available. "
                "Reinstall the app with dependencies (iphone-backup-decrypt) or use a Python environment that includes it."
            )

        self._is_encrypted = bool(passphrase and _HAS_ENCRYPTED)
        _log().info("backup_path=%s encrypted=%s", self.backup_path, self._is_encrypted)
        self._load_manifest()

    def _load_manifest(self) -> None:
        log = _log()
        if self._is_encrypted:
            self._encrypted_backup = EncryptedBackup(
                backup_directory=str(self.backup_path),
                passphrase=self.passphrase,
            )
            self._manifest_path = self.temp_dir / "Manifest.db"
            self._encrypted_backup.save_manifest_file(output_filename=str(self._manifest_path))
        else:
            self._manifest_path = self.backup_path / "Manifest.db"
        if not self._manifest_path or not self._manifest_path.exists():
            log.error("Manifest.db not found at %s", self._manifest_path)
            raise FileNotFoundError("Manifest.db not found. Is this a valid iTunes backup folder?")
        log.info("Manifest path exists: %s", self._manifest_path)
        try:
            conn = sqlite3.connect(str(self._manifest_path))
        except sqlite3.Error as e:
            msg = str(e).lower()
            if "not a database" in msg:
                raise ValueError(
                    "Cannot open Manifest.db as a SQLite database. "
                    "If this backup is encrypted, enter the correct backup password. "
                    "If you recently changed encryption, use the password that was active when this backup was made."
                ) from e
            raise
        try:
            # Discover table name (Files or files)
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND (name='Files' OR name='files')"
            )
            table_name = None
            for (name,) in cur:
                table_name = name
                break
            if not table_name:
                log.warning("Manifest: no suitable table (Files/files)")
                conn.close()
                return
            log.info("Manifest table name: %s", table_name)
            # Get column list (keep original casing for query)
            cur = conn.execute(f"PRAGMA table_info({table_name})")
            col_rows = cur.fetchall()
            cols_lower = [row[1].lower() for row in col_rows]
            file_id_col = None
            domain_col = None
            path_col = None
            for row in col_rows:
                c = row[1]
                cl = c.lower()
                if cl in ("fileid", "file_id"):
                    file_id_col = c
                elif cl == "domain":
                    domain_col = c
                elif cl in ("relativepath", "relative_path", "path"):
                    path_col = c
            if not path_col:
                for row in col_rows:
                    if row[1].lower() in ("relativepath", "relative_path", "path"):
                        path_col = row[1]
                        break
            if not file_id_col:
                for row in col_rows:
                    if row[1].lower() in ("fileid", "file_id"):
                        file_id_col = row[1]
                        break
            domain_col = domain_col or ("domain" if "domain" in cols_lower else None)
            if not file_id_col or not path_col:
                log.warning("Manifest: missing column file_id=%s path_col=%s", file_id_col, path_col)
                conn.close()
                return
            domain_col = domain_col or "domain"
            log.info("Manifest columns: fileID=%s domain=%s relativePath=%s", file_id_col, domain_col, path_col)
            cur = conn.execute(
                f'SELECT "{file_id_col}", "{domain_col}", "{path_col}" FROM "{table_name}" WHERE "{path_col}" IS NOT NULL AND "{path_col}" != \'\''
            )
            for row in cur:
                file_id, domain, rel = row[0], row[1] if len(row) > 1 else "", row[2] if len(row) > 2 else ""
                if not file_id:
                    continue
                domain = domain or ""
                rel = rel or ""
                key = (domain, rel)
                entry = _FileEntry(file_id=str(file_id), relative_path=rel, domain=domain)
                self._path_map[key] = entry
                self._file_id_map[str(file_id)] = entry
            # Also map (domain, normalized path) for paths with different separators
            for (d, rel), entry in list(self._path_map.items()):
                norm = rel.replace("\\", "/")
                if norm != rel:
                    self._path_map[(d, norm)] = entry
            count = len(self._file_id_map)
            log.info("Manifest loaded: %d file entries", count)
            if count == 0:
                log.warning("Manifest has 0 file entries")
        except Exception as e:
            log.info("Manifest load exception: %s: %s", type(e).__name__, e)
        finally:
            conn.close()

    def get_backup_info(self) -> _BackupInfo:
        """Return backup metadata (device_name, backup_date) from Info.plist when possible."""
        info_path = self.backup_path / "Info.plist"
        device_name = _read_plist_str(info_path, "Device Name", "DeviceName")
        backup_date = _read_plist_str(info_path, "Last Backup Date", "LastBackupDate")
        return _BackupInfo(device_name=device_name, backup_date=backup_date or "")

    def get_file_by_path(self, domain: str, relative_path: str) -> Optional[_FileEntry]:
        """Resolve (domain, relative_path) to an entry usable with read_file_bytes."""
        rel = relative_path.replace("\\", "/").strip()
        if rel.startswith("/"):
            rel = rel[1:]
        key = (domain.strip(), rel)
        if key in self._path_map:
            return self._path_map[key]
        # Try with original path in case we stored a variant
        key_bs = (domain.strip(), relative_path.replace("/", "\\").strip())
        if key_bs in self._path_map:
            return self._path_map[key_bs]
        _log().debug("get_file_by_path: not found domain=%r rel=%r", domain, rel[:80] if rel else "")
        return None

    def get_paths_matching(self, path_substring: str) -> List[Tuple[str, str]]:
        """Return list of (domain, relativePath) where relativePath contains path_substring (case-insensitive)."""
        out: List[Tuple[str, str]] = []
        sub = path_substring.lower()
        seen: set = set()
        for (d, rel) in self._path_map.keys():
            if sub in rel.lower() and (d, rel) not in seen:
                seen.add((d, rel))
                out.append((d, rel))
        return out

    def get_paths_ending_with(self, filename: str) -> List[Tuple[str, str]]:
        """Return list of (domain, relativePath) where relativePath ends with filename (case-insensitive)."""
        if not filename or not filename.strip():
            return []
        out: List[Tuple[str, str]] = []
        fn_lower = filename.strip().lower()
        seen: set = set()
        for (d, rel) in self._path_map.keys():
            if rel.lower().endswith(fn_lower) or rel.replace("\\", "/").lower().endswith("/" + fn_lower):
                if (d, rel) not in seen:
                    seen.add((d, rel))
                    out.append((d, rel))
        return out

    def get_file_by_id(self, file_id: str) -> Optional[_FileEntry]:
        """Resolve file by its backup fileID (e.g. 3d0d7e5fb2ce288813306e4d4636395e047a3d28 for sms.db)."""
        if not file_id:
            return None
        return self._file_id_map.get(file_id)

    def read_file_bytes_by_id(self, file_id: str) -> Optional[bytes]:
        """Read file by backup fileID. For unencrypted backups, reads directly from disk (does not require Manifest)."""
        log = _log()
        if not file_id:
            return None
        if self._is_encrypted:
            entry = self._file_id_map.get(file_id)
            return self.read_file_bytes(entry) if entry else None
        # Unencrypted: try standard layout then flat layout
        path1 = self.backup_path / file_id[:2] / file_id
        path2 = self.backup_path / file_id
        for path, label in [(path1, "subdir"), (path2, "flat")]:
            if path.exists():
                try:
                    data = path.read_bytes()
                    log.debug("read_file_bytes_by_id: %s path exists, read %d bytes", label, len(data))
                    return data
                except Exception as e:
                    log.info("read_file_bytes_by_id: %s path read failed: %s", label, e)
            else:
                log.debug("read_file_bytes_by_id: %s path does not exist: %s", label, path)
        return None

    def list_db_files(self) -> List[Tuple[str, str, str]]:
        """Return list of (file_id, domain, relativePath) for all entries whose relativePath ends with .db."""
        out: List[Tuple[str, str, str]] = []
        for (d, rel), entry in self._path_map.items():
            if rel.lower().endswith(".db") and entry.file_id:
                out.append((entry.file_id, d, rel))
        return out

    def read_file_bytes(self, entry: _FileEntry) -> Optional[bytes]:
        """Return file contents for an entry from get_file_by_path."""
        log = _log()
        if self._is_encrypted:
            if not entry.relative_path:
                return None
            try:
                data = self._encrypted_backup.extract_file_as_bytes(
                    entry.relative_path,
                    domain_like=entry.domain or None,
                )
                if data and len(data) > 0:
                    return data
                log.debug("read_file_bytes: extract_file_as_bytes returned empty for %s", entry.relative_path)
            except FileNotFoundError:
                log.debug("read_file_bytes: extract_file_as_bytes FileNotFoundError (e.g. flags!=1) for %s", entry.relative_path)
            except Exception as e:
                log.debug("read_file_bytes: extract_file_as_bytes exception %s: %s", type(e).__name__, e)
            # Fallback: library filters with flags=1; query Manifest without flags and decrypt ourselves.
            # Try entry.domain first, then MediaDomain/CameraRollDomain/HomeDomain, then relativePath-only.
            def _try_decrypt_row(file_id: str, file_bplist: bytes) -> Optional[bytes]:
                try:
                    data = self._encrypted_backup._decrypt_inner_file(file_id=file_id, file_bplist=file_bplist)
                    return data
                except AssertionError:
                    return _decrypt_file_no_size_check(
                        self._encrypted_backup, self.backup_path, file_id, file_bplist
                    )
                except ValueError as ve:
                    if "not an encrypted file" in str(ve).lower():
                        raw_path = self.backup_path / file_id[:2] / file_id
                        if raw_path.exists():
                            try:
                                return raw_path.read_bytes()
                            except Exception:
                                pass
                return None

            rel_path = entry.relative_path
            domains_to_try = [entry.domain or ""]
            if entry.domain not in ("MediaDomain", "CameraRollDomain", "HomeDomain"):
                domains_to_try.extend(["MediaDomain", "CameraRollDomain", "HomeDomain"])
            try:
                conn = sqlite3.connect(str(self._manifest_path))
                try:
                    for dom in domains_to_try:
                        if not dom:
                            continue
                        cur = conn.execute(
                            "SELECT fileID, file FROM Files WHERE domain = ? AND relativePath = ? LIMIT 1",
                            (dom, rel_path),
                        )
                        row = cur.fetchone()
                        if row:
                            file_id, file_bplist = row[0], row[1]
                            data = _try_decrypt_row(file_id, file_bplist)
                            if data:
                                log.debug("read_file_bytes: fallback decrypt ok domain=%s (%d bytes)", dom, len(data))
                                return data
                    # Last resort: relativePath only (no domain filter)
                    cur = conn.execute(
                        "SELECT fileID, file FROM Files WHERE relativePath = ? LIMIT 1",
                        (rel_path,),
                    )
                    row = cur.fetchone()
                    if row:
                        file_id, file_bplist = row[0], row[1]
                        data = _try_decrypt_row(file_id, file_bplist)
                        if data:
                            log.debug("read_file_bytes: fallback decrypt ok (path-only) (%d bytes)", len(data))
                            return data
                finally:
                    conn.close()
            except Exception as e:
                log.debug("read_file_bytes: fallback decrypt failed: %s: %s", type(e).__name__, e)
            log.info("read_file_bytes: could not extract %s", entry.relative_path)
            return None
        if not entry.file_id:
            return None
        path = self.backup_path / entry.file_id[:2] / entry.file_id
        if not path.exists():
            path = self.backup_path / entry.file_id
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except Exception:
            return None

    def close(self) -> None:
        """Release resources (e.g. encrypted backup)."""
        self._encrypted_backup = None
