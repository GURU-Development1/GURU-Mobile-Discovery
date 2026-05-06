"""
Self-contained iTunes/iOS backup parser.
Supports unencrypted backups (Manifest.db + raw files) and encrypted backups (iphone-backup-decrypt).
"""

from .parser import BackupParser
from .sms import get_messages_data
from .contacts import get_contacts_data, resolve_display_name, is_placeholder_chat_identifier

__all__ = ["BackupParser", "get_messages_data", "get_contacts_data", "resolve_display_name", "is_placeholder_chat_identifier"]
