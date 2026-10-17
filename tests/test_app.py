import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def client():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    os.environ["SECRET_KEY"] = "test-secret"
    import app as application

    application.app.config.update(TESTING=True)
    with application.app.app_context():
        application.db.drop_all()
        application.db.create_all()
    with application.app.test_client() as client:
        yield client
    os.unlink(path)


def test_matrix_levels_are_normalized():
    from app import level_for

    assert level_for(0, 0, 60, 60) == "Inicial"
    assert level_for(40, 0, 60, 60) == "Gerenciado"
    assert level_for(0, 40, 60, 60) == "Definido"
    assert level_for(40, 40, 60, 60) == "Integrado"


def test_creates_flexible_questionnaire(client):
    response = client.post("/register", data={"organization": "IFES", "email": "a@example.org", "password": "very-long-secret"}, follow_redirects=True)
    assert response.status_code == 200
    response = client.post("/questionnaires/new", data={"title": "Pesquisa", "question_count": "12"}, follow_redirects=True)
    assert b"Fator 12" in response.data
