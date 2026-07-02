import re
import unicodedata


def normalize_title(title: str) -> str:
    """
    Normalize a title for matching purposes.
    
    Args:
        title: The title to normalize
        
    Returns:
        Normalized title string for comparison
    """
    if not title:
        return ""

    # Normalize unicode characters
    normalized = unicodedata.normalize("NFKD", title)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))

    # Convert to lowercase
    normalized = normalized.lower()

    # Remove punctuation
    normalized = re.sub(r"[^\w\s]", " ", normalized)

    # Normalize whitespace
    normalized = " ".join(normalized.split())

    return normalized


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size string."""
    if size_bytes >= 1024**4:
        return f"{size_bytes / (1024**4):.2f} TB"
    elif size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.2f} GB"
    elif size_bytes >= 1024**2:
        return f"{size_bytes / (1024**2):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"