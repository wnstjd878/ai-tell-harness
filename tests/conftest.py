# style-guard:off
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# 기록이 사용자 폴더에 쌓이지 않게 시험용 빈 폴더를 쓴다
os.environ.setdefault("AI_TELL_HOME", tempfile.mkdtemp(prefix="ai-tell-test-"))
