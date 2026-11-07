"""Deletion must erase every tenant-owned record even when SQLite reuses IDs."""
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import PortfolioBase, ScreenerBase, get_db
from app.main import app
from app.portfolio.dual_core_checklist_models import CardAudit, ExecutionCard, VerifiedDayEvent
from app.portfolio.models import User
from app.screener.models import TradeChecklist
from app.shared.security import current_user_id, require_admin


def test_deleted_user_records_are_removed_before_id_reuse():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    admin = User(username='admin-delete-test', password_hash='x', role='admin')
    old = User(username='old-delete-test', password_hash='x', role='user')
    db.add_all([admin, old])
    db.commit()
    old_id, admin_id = old.id, admin.id
    for user_id in (old_id, admin_id):
        db.add_all([
            TradeChecklist(user_id=user_id, trade_date=date(2026, 11, 6), review='{"note":"private"}'),
            ExecutionCard(user_id=user_id, trade_date='2026-11-06', phase='data_check', revision=1,
                          payload='{}', created_at='x', updated_at='x'),
            CardAudit(user_id=user_id, trade_date='2026-11-06', phase='data_check', revision=1,
                      action='save', recorded_at='x', snapshot='{}'),
            VerifiedDayEvent(user_id=user_id, trade_date='2026-11-06', reason='risk', source='test',
                             reference='1', observed_at='x', recorded_at='x'),
        ])
    db.commit()

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_admin] = lambda: admin
    try:
        client = TestClient(app)
        response = client.delete(f'/api/auth/users/{old_id}')
        assert response.status_code == 200, response.text
        db.expunge(old)
        new = User(username='new-delete-test', password_hash='x', role='user')
        db.add(new)
        db.commit()
        assert new.id == old_id  # exercises the SQLite ID reuse case
        for model in (TradeChecklist, ExecutionCard, CardAudit, VerifiedDayEvent):
            assert db.scalars(select(model).where(model.user_id == new.id)).all() == []
            assert len(db.scalars(select(model).where(model.user_id == admin_id)).all()) == 1
        app.dependency_overrides[current_user_id] = lambda: new.id
        cards = client.get('/api/portfolio/dual-core/cards', params={'trade_date': '2026-11-06'})
        assert cards.status_code == 200
        assert cards.json()['cards'] == []
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()
