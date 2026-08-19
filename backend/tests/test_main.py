from fastapi.testclient import TestClient

from app.main import app


def test_app_starts_and_serves_health_check_with_scheduler_lifespan():
    """lifespan으로 스케줄러 루프를 띄워도 앱이 정상 기동/응답/종료되는지
    확인한다. run_scheduler_loop는 다음 KST 04:00까지 asyncio.sleep으로
    대기만 하므로(최대 24시간), 이 테스트는 그 sleep이 끝나길 기다리지
    않는다 — with 블록을 빠져나올 때 task.cancel()로 즉시 정리된다."""
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
