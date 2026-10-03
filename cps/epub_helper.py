# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import zipfile
from lxml import etree

default_ns = {
    'n': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'pkg': 'http://www.idpf.org/2007/opf',
}


def _strip_xml_leading_noise(data):
    if isinstance(data, bytes):
        data = data.lstrip(b"\xef\xbb\xbf\r\n\t ")
        xml_index = data.find(b"<?xml")
        if xml_index > 0:
            data = data[xml_index:]
    else:
        data = data.lstrip("\ufeff\r\n\t ")
        xml_index = data.find("<?xml")
        if xml_index > 0:
            data = data[xml_index:]
    return data


def get_content_opf(file_path, ns=None):
    if ns is None:
        ns = default_ns
    epubZip = zipfile.ZipFile(file_path)
    txt = epubZip.read('META-INF/container.xml')
    # Some EPUBs include a BOM or stray whitespace before the XML declaration,
    # which causes lxml to error with: "XML declaration allowed only at the start".
    txt = _strip_xml_leading_noise(txt)
    tree = etree.fromstring(txt)
    cf_name = tree.xpath('n:rootfiles/n:rootfile/@full-path', namespaces=ns)[0]
    cf = epubZip.read(cf_name)
    cf = _strip_xml_leading_noise(cf)

    return etree.fromstring(cf), cf_name
