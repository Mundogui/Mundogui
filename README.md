# SIAPD Matrix SaaS

MVP multiempresa para criar diagnósticos de maturidade, coletar respostas e apresentar o resultado numa matriz 2x2. Ele substitui o modelo rígido de 24 colunas por perguntas configuráveis, preservando os quatro quadrantes: **Inicial**, **Gerenciado**, **Definido** e **Integrado**.

## Banco de dados recomendado

Use **PostgreSQL gerenciado** em produção (RDS, Cloud SQL, Neon ou Supabase), não SQLite. PostgreSQL oferece concorrência real, backups ponto-no-tempo, replicação, criptografia em repouso e controle de acesso por função. O esquema normalizado isola dados por `organization_id`; em uma evolução de alto volume, habilite também *row-level security* no PostgreSQL para impedir que uma conexão de uma organização enxergue outra por engano.

Entidades principais: organizações, usuários, questionários, perguntas, respostas e assinaturas. Isso torna flexível a quantidade de perguntas e evita mudanças de coluna/migrações sempre que um cliente cria um questionário.

## Executar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="troque-por-uma-chave-aleatoria-longa"
export DATABASE_URL="sqlite:///siapd.db" # somente desenvolvimento
flask --app app run --debug
```

Para produção, defina `DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/siapd`, use HTTPS, uma `SECRET_KEY` aleatória e execute atrás de um proxy reverso. Nunca grave chaves OpenAI ou Stripe no código ou em commits.

## Monetização com Stripe

Cadastre no Stripe os preços recorrentes e configure:

```bash
export STRIPE_SECRET_KEY=sk_live_...
export STRIPE_PRICE_PRO=price_...
export STRIPE_PRICE_BUSINESS=price_...
export STRIPE_WEBHOOK_SECRET=whsec_...
```

O botão de upgrade cria uma sessão Stripe Checkout. O endpoint `/billing/webhook` valida a assinatura do evento antes de alterar o plano. Configure os eventos `checkout.session.completed` e `customer.subscription.deleted`. Enquanto as chaves não estiverem configuradas, o aplicativo mostra uma mensagem de configuração em vez de simular pagamento.

Planos sugeridos: **Grátis** (1 questionário, 100 respostas/mês), **Pro** (10 questionários, 2.000 respostas/mês, exportação) e **Business** (uso ampliado, equipe e marca branca). Os limites são aplicados no servidor quando um questionário é criado ou uma resposta é enviada.
