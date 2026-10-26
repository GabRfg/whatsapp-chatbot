# Chatbot de WhatsApp configurável

*[English version](README.en.md)*

Um bot de atendimento no WhatsApp cujo conteúdo vive num arquivo YAML, não no
código. Para atender outra empresa, troque o arquivo — nenhuma linha de Python
muda.

Construído com FastAPI, Redis e [WAHA](https://waha.devlike.pro) como provedor
de WhatsApp.

```yaml
states:
  menu:
    text: |
      Olá! Bem-vindo ao atendimento da *Acme*.
      1️⃣ Suporte técnico
      2️⃣ Comercial
    options:
      "1":
        text: "Descreva seu problema. Um técnico assumirá a conversa."
        next: handoff
      "2":
        text: "Catálogo: https://exemplo.com.br/produtos"
        next: menu
```

## Por que separar o fluxo do código

A versão anterior deste projeto tinha o menu, os textos e as transições dentro
de uma cadeia de `if/elif` no `main.py`. Funcionava para uma empresa, e só.

Separar as duas coisas resolve mais que o reuso:

- **O conteúdo deixa de exigir programador.** Quem conhece o atendimento edita o YAML
- **A cadeia de condicionais desaparece**, e com ela a chance de dois caminhos divergirem
- **O motor fica testável** sem WhatsApp, sem Redis e sem rede
- **Erros de fluxo aparecem na subida**, não na conversa de um cliente

## Começando

```bash
git clone <seu-repositorio>
cd whatsapp-chatbot

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # cole em WEBHOOK_SECRET

docker compose up -d --build
```

Depois, no dashboard da WAHA (http://127.0.0.1:3000):

1. Leia o QR Code para ativar a sessão
2. Configure o webhook para `http://bot:8000/webhook`
3. Em **Events**, deixe apenas `message` ativo
4. Adicione o cabeçalho `X-Webhook-Token` com o mesmo valor de `WEBHOOK_SECRET`

Para criar o seu fluxo, copie `flows/example.yml`, edite, e aponte `FLOW_PATH`
para a cópia.

## Formato do fluxo

### `bot`

| Campo | Para que serve |
| --- | --- |
| `name` | Nome do bot, usado em logs |
| `session` | Nome da sessão na WAHA |
| `entrypoint` | Estado em que todo usuário novo entra |
| `state_ttl` | Segundos até o estado do usuário expirar (60 a 86400) |
| `reset_keywords` | Palavras que voltam ao início de qualquer lugar |
| `back_hint` | Texto anexado ao fim das respostas, exceto no menu principal |
| `fallback` | Resposta quando nada corresponde |
| `unsupported_media` | Resposta quando chega áudio, imagem ou figurinha |

### `states`

Cada estado é um ponto da conversa. Ao entrar num estado, o bot envia o `text`
dele e passa a aceitar as chaves de `options`.

```yaml
nome_do_estado:
  text: "Mensagem enviada ao entrar aqui"
  options:
    "1":
      label: "Rótulo opcional, só para documentar"
      text: "Resposta a esta escolha"
      next: outro_estado      # vazio mantém o usuário onde está
```

### `handoff`

`next: handoff` entrega a conversa a um atendente humano: o bot silencia e não
responde mais nada, nem a mídia. A única saída é uma `reset_keyword`, o que
impede o bot de interromper um atendimento em andamento.

`handoff` é embutido e não se declara em `states`. Para dar outro nome a esse
comportamento, crie um estado com `handoff: true`.

### Validação

O fluxo é validado quando o container sobe. São recusados: `next` apontando
para estado inexistente, `entrypoint` que não existe, estados inalcançáveis,
campos desconhecidos, `reset_keywords` vazio e redefinição de nomes reservados.

Um erro de digitação derruba a subida, em vez de levar um cliente a um beco sem
saída.

## Segurança

**O webhook exige autenticação.** Sem `WEBHOOK_SECRET`, a aplicação se recusa a
subir. Isso é deliberado: um webhook aberto permite que qualquer pessoa que
alcance a porta faça o número conectado enviar mensagens para qualquer destino.

São aceitas duas provas de origem:

| Mecanismo | Cabeçalho | Garante |
| --- | --- | --- |
| Token fixo | `X-Webhook-Token` | Que a requisição conhece o segredo |
| HMAC-SHA256 do corpo | `X-Webhook-Hmac` | Origem **e** que o corpo não foi alterado |

A comparação usa `compare_digest`, para que o tempo de resposta não revele
quantos caracteres do segredo foram acertados.

**Outras medidas:**

- Nenhum conteúdo de mensagem é persistido. O Redis guarda só o nome do estado
  e um contador, ambos com expiração
- Telefones aparecem em log como hash curto, nunca em claro
- Limite de mensagens por usuário, por janela de tempo
- Payload validado com Pydantic: formato inesperado vira 422, não 500
- Redis indisponível vira 503, para o provedor reenviar — sem stack trace na resposta
- O container roda como usuário sem privilégios, com o código somente leitura
- O Redis não publica porta; a WAHA publica apenas em `127.0.0.1`
- `.gitignore` e `.dockerignore` cobrem `.env` e `sessions/`

Sobre `sessions/`: essa pasta contém o estado de autenticação do WhatsApp. Quem
a obtiver consegue se passar pelo número conectado. Ela nunca deve ser
versionada nem entrar numa imagem.

## Arquitetura

```
WhatsApp  ──►  WAHA  ──►  POST /webhook
                             │
                     security.verify()      token ou HMAC
                             │
                     schemas.WebhookEvent   validação do payload
                             │
                     state.StateStore       estado + limite (Redis)
                             │
                     engine.decide()        função pura: estado + texto → resposta
                             │
                     providers.waha         envio
```

| Arquivo | Responsabilidade |
| --- | --- |
| `app/main.py` | HTTP, autenticação e acoplamento. Nenhuma regra de conversa |
| `app/engine.py` | A máquina de estados. Função pura, sem I/O |
| `app/flow.py` | Carga e validação do YAML |
| `app/schemas.py` | Formato do webhook recebido |
| `app/security.py` | Verificação de token e HMAC |
| `app/state.py` | Estado por usuário e limitador, no Redis |
| `app/providers/` | Abstração do provedor; `waha.py` é a implementação atual |
| `app/config.py` | Configuração por ambiente, com falha fechada |

Trocar a WAHA por Twilio ou pela Cloud API da Meta é escrever outra classe com
`send_text` e `close`.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

62 testes, sem WhatsApp e sem Redis real. Cobrem a validação de fluxo, a
máquina de estados, a autenticação do webhook, os payloads que antes derrubavam
o serviço, o limitador e a queda do Redis.

## Limitações conhecidas

- **Só responde texto.** Mídia recebida gera uma resposta explicativa; o bot não
  processa imagem, áudio nem localização
- **Sem interface de administração.** Editar o fluxo é editar o YAML e reiniciar
- **Sem métricas.** Há `/health`, mas nada exportado para Prometheus
- **Limitador por janela fixa.** Simples e suficiente aqui; uma rajada na virada
  da janela passa
- **Um provedor implementado.** A abstração existe, mas só a WAHA foi escrita

## Licença

MIT
