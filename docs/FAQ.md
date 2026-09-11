# FAQ do autograde

Esta página é organizada pelo **que aparece na sua tela**, não pela ordem do
tutorial. Achou uma mensagem de erro? Procure por ela aqui (Ctrl+F).

> **Antes de qualquer coisa, rode:**
>
> ```bash
> autograde doctor
> ```
>
> Ele checa Python, git, `gh`, login, turma e o repositório atual de uma vez, e
> imprime o comando exato que conserta cada item que estiver faltando. A maior
> parte das perguntas abaixo é respondida por ele em 5 segundos.

---

## Índice rápido

| Se você viu… | Vá para |
|---|---|
| `turma_not_eligible` | [turma_not_eligible](#turma_not_eligible) |
| `not_in_roster` / `403` no login | [not_in_roster](#not_in_roster) |
| `repo_owner_mismatch` | [repo_owner_mismatch](#repo_owner_mismatch) |
| `Não está num repo git` | [não estou num repo](#nao_estou_num_repo) |
| `exercise_not_found` / `404` | [exercise_not_found](#exercise_not_found) |
| `exercise_not_open_yet` | [exercise_not_open_yet](#exercise_not_open_yet) |
| `invalid_repo_url` | [invalid_repo_url](#invalid_repo_url) |
| `repo_url_required` | [repo_url_required](#repo_url_required) |
| `gh not found in PATH` | [gh não encontrado](#gh_nao_encontrado) |
| `invalid_shell_evidence` | [invalid_shell_evidence](#invalid_shell_evidence) |
| `HTTP 429` | [limites de tentativa](#rate_limit) |
| `github_unavailable` / `502` | [github_unavailable](#github_unavailable) |
| `500`, `502`, `503` | [erros do servidor](#erro_5xx) |
| `token_expired` / `401` | [sessão expirada](#token_expired) |
| Erro de conexão / timeout | [erro de rede](#erro_de_rede) |
| A CLI ficou parada esperando algo | [perguntas de reflexão](#perguntas) |
| Critério falhou mas "está tudo certo" | [meu boletim está errado](#boletim_errado) |
| `invalid_github_username` / username errado | [meu cadastro do GitHub](#perfil) |

---

<a id="turma_not_eligible"></a>

## `turma_not_eligible` — "não estou na turma deste exercício"

**O que significa:** o exercício que você pediu pertence a uma turma diferente
da sua.

**O que NÃO resolve:** `autograde login`. Sua turma vem da **planilha do
roster** que o professor mantém — não do seu login Google. Refazer o login te
devolve exatamente o mesmo erro.

**O que fazer:**

1. Veja em que turma você está:

   ```bash
   autograde whoami
   ```

2. Compare com a turma do exercício. Ela está no topo do YAML, em
   `exercicios/<id>.yaml` do repositório do curso:

   ```yaml
   turmas:
     - IA-2026-01
   ```

3. **Se você digitou o id errado**: os dois cursos têm exercícios com o mesmo
   número. `1.3` é Transformação Digital; `ia-1.3` é Agentes de IA. Rode com o
   id certo.

4. **Se a turma está errada no seu cadastro**: peça ao professor para corrigir
   a coluna `turma` da sua linha no roster.

### Faço as duas disciplinas. Preciso de dois cadastros?

Não. A coluna `turma` aceita **mais de uma turma**, separadas por `;`:

| email | nome | turma | github_username |
|---|---|---|---|
| ana@aluno.idp.edu.br | Ana Silva | `TD-2026-01;IA-2026-01` | anasilva |

Com isso a mesma conta valida exercícios dos dois cursos. Cada submissão é
gravada na planilha com a turma **do exercício** — os relatórios por turma
continuam separados. (`,` e `\|` também funcionam como separador.)

**Por que aparecem duas turmas se é a mesma sala?** Em 2026/1, `TD-2026-01` e
`IA-2026-01` são a mesma sala de alunos, cursando duas disciplinas diferentes.
O nome da turma identifica **disciplina + turma**, não a sala — por isso são
dois rótulos para as mesmas pessoas, e por isso a sua linha traz os dois.

---

<a id="not_in_roster"></a>

## `not_in_roster` — "meu email não está na planilha da turma"

**Causa mais comum:** você fez `autograde login` com o **gmail pessoal** em vez
do email institucional. O backend cruza o email do login com a planilha da
turma; um email que não está lá é rejeitado.

```bash
autograde whoami          # com qual email estou logado?
autograde login           # refazer, escolhendo a conta certa
```

Se o email está certo e ainda assim aparece o erro, você ainda não foi
incluído no roster — fale com o professor.

---

<a id="repo_owner_mismatch"></a>

## `repo_owner_mismatch` — "este repositório não é seu"

O backend compara o **dono do repositório** com o `github_username` do seu
cadastro. Se não bate, ele recusa (é o que impede submeter o repo de um colega).

```bash
git config --get remote.origin.url   # de quem é o repo deste diretório?
autograde whoami                     # qual username está no meu cadastro?
gh auth status                       # com qual conta GitHub estou logado?
```

Três causas, em ordem de frequência:

1. Você está no diretório de **outro** repositório. `cd` para o certo.
2. Você criou o repo com uma segunda conta do GitHub. Use `gh auth switch`.
3. O roster tem o username errado — peça a correção ao professor.

---

<a id="nao_estou_num_repo"></a>

## "Você não está no diretório do repositório do exercício"

O `autograde` descobre **qual** repositório avaliar lendo o `origin` do git do
diretório em que você está. Fora de um repositório clonado, ele não tem o que
avaliar.

```bash
cd caminho/para/meu-primeiro-repo    # a pasta que tem .git dentro
git config --get remote.origin.url   # tem que imprimir a URL do seu repo
autograde validar ia-1.1
```

Ainda não clonou?

```bash
gh repo clone SEU-USUARIO/NOME-DO-REPO
cd NOME-DO-REPO
```

> Rode `autograde validar` num terminal **normal**, não de dentro do agente de
> IA (Claude Code, Codex, Cursor). O exercício pede a sua reflexão, com as suas
> palavras.

---

<a id="exercise_not_found"></a>

## `exercise_not_found` — "não existe exercício com esse id"

Quase sempre é o prefixo do curso:

| Curso | Formato | Exemplos |
|---|---|---|
| Agentes de IA | **com** prefixo `ia-` | `ia-1.1`, `ia-1.2`, `ia-1.3`, `ia-1.4` |
| Transformação Digital | **sem** prefixo | `1.1`, `1.2`, `2.1`, `4.1` |

`autograde validar 1.3` e `autograde validar ia-1.3` são exercícios
**diferentes**. A lista completa está na pasta `exercicios/` do repositório do
seu curso.

---

<a id="exercise_not_open_yet"></a>

## `exercise_not_open_yet` — "o exercício ainda não abriu"

Não há nada a consertar do seu lado. A mensagem traz a data de abertura; volte
depois dela. A data está no campo `disponivel_a_partir_de` do YAML.

---

<a id="invalid_repo_url"></a>

## `invalid_repo_url` — "o origin não é um repo do GitHub"

```bash
git config --get remote.origin.url
```

Se isso imprimir um caminho local, um repositório de outro serviço, ou nada,
você está no diretório errado — ou clonou de um lugar que o autograder não
consegue avaliar. O exercício precisa de um repositório hospedado no GitHub, no
seu usuário.

---

<a id="repo_url_required"></a>

## `repo_url_required` — "este exercício precisa de um repositório"

O backend recebeu uma submissão sem `repo_url` para um exercício que exige
repositório. Normalmente você rodou `autograde validar` de uma pasta que não é
um repositório git, ou de uma que é mas não tem `origin`:

```bash
git config --get remote.origin.url   # tem que imprimir uma URL do GitHub
```

Se a pasta ainda não é um repositório:

```bash
git init
git add . && git commit -m "primeira versão"
gh repo create --source=. --public --push
```

**Nem todo exercício exige repositório.** Os de git (aula 1) exigem, porque o
repositório *é* o conteúdo avaliado. Outros são corrigidos só pelos arquivos da
sua máquina e por comandos rodados nela — nesses, o `autograde validar` funciona
de qualquer pasta, versionada ou não. Quem decide é o YAML do exercício, no
campo `requer_repositorio:` (default `true`).

---

<a id="gh_nao_encontrado"></a>

## `gh not found in PATH` — perdi 40 pontos sem entender por quê

A partir do exercício 1.2, parte da nota vem de **evidência local**: a CLI roda
`gh --version`, `gh auth status` e `gh repo view` na sua máquina e envia o
resultado. Sem o `gh` instalado, esses critérios zeram.

```bash
# Windows
winget install --id GitHub.cli
# macOS
brew install gh
# Linux: https://cli.github.com
```

Depois **abra um terminal novo** (o `PATH` só é relido em sessão nova) e:

```bash
gh auth login    # GitHub.com > HTTPS > Y > Login with a web browser
gh auth status   # tem que dizer "Logged in to github.com"
```

Confirme tudo com `autograde doctor`.

---

<a id="invalid_shell_evidence"></a>

## `invalid_shell_evidence` — "a evidência foi rejeitada"

Praticamente sempre é **CLI desatualizada**: o exercício passou a coletar um
comando que a sua versão ainda não conhece (ou vice-versa).

```bash
cd caminho/para/autograde-idp
git pull
pip install -e .
autograde --version
```

---

<a id="rate_limit"></a>

## `HTTP 429` — limites de tentativa

Existem dois limites, por exercício:

| Limite | Valor | Reset |
|---|---|---|
| Intervalo entre tentativas | 30 segundos | imediato |
| Tentativas por dia | 10 | meia-noite, horário de Brasília |

Nenhum deles apaga nota: suas submissões anteriores continuam valendo e **a
maior nota é a que conta**. Se bateu no limite, espere e rode de novo.

Para gastar menos tentativas: rode `autograde validar <id>`, leia o boletim e
responda **`n`** quando ele perguntar se quer submeter. Corrija o que falhou e
só responda `s` quando estiver satisfeito.

---

<a id="github_unavailable"></a>

## `github_unavailable` — o backend não conseguiu ler meu repositório

O backend consulta o GitHub pela API pública. Ele **não vê** o que está só na
sua máquina.

1. **O repositório é público?** Settings > General > Danger Zone > Change
   visibility > Public. Repositório privado é a causa nº 1.
2. **Você deu `git push`?** Commit local não conta.

   ```bash
   git status    # "nothing to commit" E "up to date with 'origin/main'"
   git push origin main
   ```

3. **O repo existe com esse nome?** `gh repo view`
4. Se tudo acima está certo, foi instabilidade do GitHub — tente de novo.

---

<a id="erro_5xx"></a>

## `500`, `502`, `503` — erro do servidor

Não é problema seu e nada foi perdido. Tente de novo em alguns minutos.

Se você estava **submetendo** quando aconteceu: rodar `autograde validar <id>`
de novo é seguro. A CLI guarda o identificador da tentativa em
`~/.git-exercicios/in-flight.json` e o reusa, então a sua nota não vira duas
linhas na planilha.

Se persistir por mais de alguns minutos, avise o professor.

---

<a id="token_expired"></a>

## `401` / `token_expired` — sessão expirada

```bash
autograde login
```

O token do Google vale alguns meses. Ele fica em
`~/.git-exercicios/token.json` — se você apagou o arquivo, é só logar de novo.

---

<a id="erro_de_rede"></a>

## Erro de rede / timeout

A CLI não conseguiu falar com o backend.

1. Confira sua conexão.
2. Rede corporativa ou VPN pode estar bloqueando — teste em outra rede.
3. Exercícios com correção por LLM demoram: a CLI espera até 3 minutos. Se sua
   rede for muito lenta, aumente:

   ```bash
   AUTOGRADE_HTTP_TIMEOUT=300 autograde validar ia-1.1
   ```

---

<a id="perguntas"></a>

## Perguntas de reflexão

A partir do `ia-1.1`, antes do boletim a CLI faz uma pergunta e espera você
escrever a resposta. Ela é corrigida por uma LLM e vale pontos.

- **Resposta em branco é rejeitada** — a CLI repete a pergunta até você
  responder.
- **Precisa ser interativo.** Em pipe, redirect de stdin, ou dentro do terminal
  do agente de IA, a CLI não consegue perguntar. Rode num terminal normal.
- **Não copie a saída do terminal.** O critério de correção rejeita
  explicitamente copy-paste de output e respostas evasivas ("o agente fez
  tudo"). Explique com as suas palavras o que pelo menos 2 comandos fizeram.
- Nos exercícios com agente de IA (`ia-1.3`, `ia-1.4`), colar **o prompt que
  você deu ao agente** é aceito — é a parte (1) da resposta. A parte (2), a
  explicação dos comandos, tem que ser sua.

---

<a id="perfil"></a>

## Meu cadastro do GitHub (`github_username`)

O autograder só aceita um repositório se o **dono dele** for o
`github_username` da sua linha no roster. É isso que impede alguém submeter o
repositório de um colega.

Para ver e completar seu cadastro:

```bash
autograde perfil
```

- **Se estiver vazio**, o comando pergunta e grava. Ele também é perguntado no
  primeiro `autograde login` — mas só quando o terminal é interativo, então
  quem logou por script pode ter pulado essa etapa.
- **Se estiver preenchido e errado**, só o professor consegue corrigir. A
  planilha recusa sobrescrever uma célula já preenchida de propósito: sem essa
  regra, um aluno poderia cravar o username de outro. Peça a correção citando o
  username certo.

Sintoma de cadastro errado: `repo_owner_mismatch` ao validar. Veja
[repo_owner_mismatch](#repo_owner_mismatch).

---

<a id="boletim_errado"></a>

## "Esse critério devia ter passado"

O backend **não enxerga** a sua máquina — ele consulta o GitHub pela API. Antes
de reportar um bug, confira nesta ordem:

1. **Você fez push?**

   ```bash
   git status
   # "nothing to commit, working tree clean"
   # "Your branch is up to date with 'origin/main'"
   ```

2. **O repositório é público?** (Settings > Change visibility)
3. **Você está no diretório certo?**
   `git config --get remote.origin.url` tem que apontar para o repo **deste**
   exercício.
4. **PR mergeado é diferente de PR aberto.** O critério `pr_mergeado` exige a
   tag roxa **Merged** na aba Pull requests. Fechado sem merge não conta.
5. **Critério de "commit recente"** exige commit nas últimas 24h. Repositório
   feito semana passada precisa de um commit novo:

   ```bash
   echo "" >> README.md && git commit -am "docs: atualiza" && git push
   ```

Continua estranho? Descreva no canal da disciplina, com o id do exercício e o
boletim completo que a CLI imprimiu.

---

## Outras perguntas

**Posso submeter várias vezes?**
Sim, sem limite de tentativas ao longo do semestre (respeitado o limite diário).
A **maior** nota é a que conta.

**Como vejo minhas notas?**

```bash
autograde notas
```

**Submeti depois do prazo. Perdi pontos?**
A submissão é aceita e marcada como atrasada na planilha. O desconto (se
houver) é decisão do professor.

**Windows nativo funciona?**
Sim para `ia-1.1` e `ia-1.2`. Para os exercícios com agente de IA, prefira o
**WSL2** — Claude Code e Codex CLI se comportam de forma diferente no Windows
nativo.

**Onde fica meu token?**
`~/.git-exercicios/token.json` (no Windows, `C:\Users\SEU-USUARIO\.git-exercicios\`).

**Comandos que existem:**

```bash
autograde doctor              # diagnóstico do ambiente (comece por aqui)
autograde login               # autenticar
autograde whoami              # email, turma(s), username do GitHub
autograde perfil              # ver/completar o cadastro do GitHub
autograde validar <id>        # boletim + prompt de submissão
autograde validar <id> --auto-submit
autograde notas               # histórico
autograde --version
```
