# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Build a minimal valid EPUB on the fly for the ingest integration tests
(``from fixtures.generate_synthetic import create_minimal_epub``).
"""

import zipfile
from pathlib import Path


def create_minimal_epub(output_path: Path) -> None:
    """
    Create the smallest possible valid EPUB file (~2-3 KB).

    This file has the absolute minimum structure required by the EPUB spec:
    - mimetype file
    - META-INF/container.xml
    - content.opf (package document)
    - Single HTML content file
    """
    print(f"Creating minimal valid EPUB: {output_path.name}")

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # 1. mimetype (MUST be first, MUST be uncompressed)
        epub.writestr(
            'mimetype',
            'application/epub+zip',
            compress_type=zipfile.ZIP_STORED  # No compression!
        )

        # 2. META-INF/container.xml (points to content.opf)
        epub.writestr('META-INF/container.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>''')

        # 3. content.opf (package document with minimal metadata)
        epub.writestr('content.opf', '''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-minimal-001</dc:identifier>
    <dc:title>Minimal Test Book</dc:title>
    <dc:creator>CWA Test Suite</dc:creator>
    <dc:language>en</dc:language>
    <dc:date>2025-01-01</dc:date>
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="content"/>
  </spine>
</package>''')

        # 4. content.xhtml (minimal HTML content)
        epub.writestr('content.xhtml', '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Test Content</title>
</head>
<body>
  <h1>Minimal Test Book</h1>
  <p>This is a minimal valid EPUB file for testing purposes.</p>
  <p>It contains only the required structural elements.</p>
</body>
</html>''')

    size = output_path.stat().st_size
    print(f"  ✓ Created ({size:,} bytes)")
