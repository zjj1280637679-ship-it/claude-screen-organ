# -*- coding: utf-8 -*-
import os
import sys

import pytest

MCP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
sys.path.insert(0, MCP_DIR)


@pytest.fixture(scope="session")
def organ():
    from organ import (capture, imaging, privacy, quality, store, textread,  # noqa
                       watch, wininfo)
    return {
        "capture": capture, "imaging": imaging, "privacy": privacy,
        "quality": quality, "store": store, "textread": textread,
        "watch": watch, "wininfo": wininfo,
    }
