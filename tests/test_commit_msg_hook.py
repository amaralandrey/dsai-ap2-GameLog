"""Hook .githooks/commit-msg: trailers Agent: e Spec: obrigatórios (regras 2 e 6 do plano da AP2)."""
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "commit-msg"
SPEC = "SPEC/2026-11-07-comentarios.md"


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@ex.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "SPEC").mkdir()
    (tmp_path / SPEC).write_text("# spec\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "spec inicial")
    return tmp_path


def run_hook(repo, msg):
    (repo / "MSG").write_text(msg)
    return subprocess.run(["bash", str(HOOK), "MSG"], cwd=repo, capture_output=True, text=True)


def test_aceita_trailers_com_spec_commitada(repo):
    r = run_hook(repo, f"feat: x\n\nAgent: claude-code/claude-opus-5-5\nSpec: {SPEC}\n")
    assert r.returncode == 0, r.stderr


def test_aceita_agent_none_e_spec_none(repo):
    assert run_hook(repo, "docs: x\n\nAgent: none\nSpec: none\n").returncode == 0


def test_ignora_linhas_de_comentario(repo):
    assert run_hook(repo, "docs: x\n\nAgent: none\nSpec: none\n# Please enter the commit message\n").returncode == 0


@pytest.mark.parametrize("titulo", ["Merge branch 'x'", "Revert \"feat: x\"", "fixup! feat: x", "squash! feat: x"])
def test_dispensa_merge_revert_fixup_e_squash(repo, titulo):
    assert run_hook(repo, f"{titulo}\n").returncode == 0


@pytest.mark.parametrize(
    "msg, erro",
    [
        ("feat: x\n\nSpec: none\n", "falta o trailer 'Agent:'"),
        ("feat: x\n\nAgent: none\n", "falta o trailer 'Spec:'"),
        ("feat: x\n", "falta o trailer 'Agent:'"),
        ("feat: x\n\nAgent: none\nSpec: SPEC/comentarios.md\n", "não segue o formato"),
        ("feat: x\n\nAgent: none\nSpec: SPEC/2026-01-01-inexistente.md\n", "não existe no repositório"),
    ],
)
def test_recusa_trailers_ausentes_ou_invalidos(repo, msg, erro):
    r = run_hook(repo, msg)
    assert r.returncode == 1
    assert erro in r.stderr


def test_trailer_no_meio_do_corpo_nao_conta(repo):
    r = run_hook(repo, "feat: x\n\nAgent: none\nSpec: none\n\nparágrafo final sem trailers\n")
    assert r.returncode == 1


def test_aceita_spec_nova_commitada_sozinha(repo):
    (repo / "SPEC/2026-11-15-nova.md").write_text("# nova\n")
    git(repo, "add", "SPEC/2026-11-15-nova.md")
    assert run_hook(repo, "docs(spec): nova\n\nAgent: none\nSpec: SPEC/2026-11-15-nova.md\n").returncode == 0


def test_recusa_spec_nova_junto_com_codigo(repo):
    (repo / "SPEC/2026-11-15-nova.md").write_text("# nova\n")
    (repo / "app.py").write_text("x = 1\n")
    git(repo, "add", ".")
    r = run_hook(repo, "feat: nova\n\nAgent: none\nSpec: SPEC/2026-11-15-nova.md\n")
    assert r.returncode == 1
    assert "commite a spec sozinha antes do código" in r.stderr
