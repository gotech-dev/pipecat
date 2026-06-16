"""Cấu hình pytest cho test màn hình /minutes.

Thêm thư mục example (cha của tests/) vào sys.path để import được
minutes_bot, minutes_history_service, history_service... như khi chạy thật.
"""

import os
import sys

EXAMPLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXAMPLE_DIR not in sys.path:
    sys.path.insert(0, EXAMPLE_DIR)
