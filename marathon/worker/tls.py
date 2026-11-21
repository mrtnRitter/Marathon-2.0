# IMPORTS
import ssl
from pathlib import Path

import truststore


# FUNC
def create_ssl_context(ca_file: Path | None = None) -> ssl.SSLContext:
    """Create a TLS client context that verifies server certificates and names."""
    if ca_file is not None:
        try:
            context = ssl.create_default_context(cafile=str(ca_file))
        except (OSError, ssl.SSLError) as error:
            raise ValueError(f"CA-Datei nicht lesbar: {ca_file}") from error
    else:
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context
