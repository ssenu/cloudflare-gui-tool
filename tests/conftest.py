from __future__ import annotations

import os

# GUI(PyQt6) 테스트를 CI/서버 등 디스플레이 없는 환경에서도 돌리기 위해
# 명시적으로 platform을 지정하지 않은 경우에만 offscreen으로 강제한다.
# 사용자가 이미 QT_QPA_PLATFORM을 설정했다면(예: 로컬에서 직접 확인) 존중한다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
