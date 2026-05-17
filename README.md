# autograde-idp

CLI cliente do **Autograder IDP-TD** — login Google via Device Code Flow, validação local e submissão de exercícios para o backend ([autograde-idp-backend](https://github.com/alexlopespereira/autograde-idp-backend)).

Plataformas suportadas: Linux, macOS, Windows. Python ≥ 3.9.

---

## Instalação (aluno)

### 1. Python 3.9+

- macOS / Linux: já vem instalado, ou `brew install python` / `apt install python3`.
- Windows: baixar de [python.org](https://www.python.org/downloads/) e marcar
  "Add Python to PATH" no instalador.

Verifique:

```bash
python --version    # Windows / venvs ativos
python3 --version   # macOS / Linux
```

### 2. Clone e instale a CLI

```bash
git clone https://github.com/alexlopespereira/autograde-idp.git
cd autograde-idp
pip install -e .
```

Isso registra o entry-point `autograde` no PATH.

O `client_id` OAuth do projeto TD-2026 já vem embutido no código — você não
precisa configurar nada para `autograde login`. O CLI fala com o `/device/code`
do Google diretamente (só client_id necessário) e delega o `/token` exchange
ao backend, que guarda o `client_secret` no Secret Manager — assim a CLI nunca
toca no secret.

Para apontar a CLI a outro projeto Google (ex.: desenvolvimento contra
staging), exporte `GOOGLE_OAUTH_CLIENT_ID` antes do `autograde login` (o
backend de staging precisa estar configurado para o mesmo client_id).

### 3. GitHub CLI (`gh`)

A partir do **Exercício 1.2** o autograder coleta evidência local de
autenticação e operação no GitHub via `gh`. Instale antes de submeter:

- **Site oficial:** [cli.github.com](https://cli.github.com)
- **macOS (Homebrew):** `brew install gh`
- **Windows (winget):** `winget install --id GitHub.cli`
- **Windows (scoop):** `scoop install gh`
- **Debian/Ubuntu:** ver [instruções oficiais](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)

Depois de instalar, autentique uma vez:

```bash
gh auth login
```

Verifique:

```bash
gh --version
gh auth status
```

> Se `gh` não estiver no PATH, o autograder marca os critérios
> `gh_authenticated`, `gh_version_capturado` e `gh_repo_view_ok` como falhos
> com mensagem `gh not found in PATH`. Isso é o comportamento esperado e não
> impede a coleta — apenas zera esses critérios na nota.

### 4. Autenticação Google (uma vez por máquina)

```bash
autograde login
```

Abre Device Code Flow no navegador. Token persistido em
`~/.git-exercicios/token.json` (chmod 0600 em Unix). Refresh é automático;
re-login obrigatório a cada ~5 meses.

---

## Uso

Dentro do diretório do repo do exercício (precisa ter `remote origin`):

```bash
autograde validar 1.2
```

A CLI mostra o boletim e pergunta `Deseja submeter? (s/n)`. Use
`--auto-submit` para pular o prompt em scripts.

Outros comandos:

```bash
autograde whoami          # mostra usuário logado + turma
autograde notas           # lista notas já submetidas
autograde login           # re-autenticar
```

---

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
```

CI roda matrix Linux/macOS/Windows automaticamente em PRs.

---

## Arquitetura

A CLI é um **cliente fino**: apenas autentica, coleta evidência local
(shell, arquivos do repo) e bate em endpoints do backend. A nota é decidida
pelo backend (juiz independente, stateless) — ver
[autograde-idp-backend](https://github.com/alexlopespereira/autograde-idp-backend).

Decisões de design completas: [autograder-design.md](https://github.com/alexlopespereira/assistente-aulas/blob/main/autograde/autograder-design.md).

## Licença

MIT.
