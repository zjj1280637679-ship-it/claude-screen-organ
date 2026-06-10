# -*- coding: utf-8 -*-
"""screen-organ 核心库。import 本包的第一件事是设置 DPI 感知。"""
from .dpi import ensure_dpi_awareness

DPI_MODE = ensure_dpi_awareness()
