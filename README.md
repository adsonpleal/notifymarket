# notifymarket

Monitora preços de itens no shop-search do Ragnarok Online LATAM (`ro.gnjoylatam.com`) e envia uma notificação push para o seu celular quando um item atinge o preço-alvo que você definiu.

## Como funciona

- Um workflow do GitHub Actions roda a cada 15 minutos.
- Para cada item listado em `config.yaml`, o script `watch.py` consulta a página de shop-search no servidor configurado e identifica o menor preço de COMPRA.
- Os preços atuais (e o vendedor + nome do comércio do menor) são impressos nos logs do job — você pode acompanhar acessando a aba **Actions** do repositório.
- Se o menor preço for menor ou igual ao `target_price`, o script envia uma notificação push via [ntfy.sh](https://ntfy.sh) com o nome do item, preço, vendedor e um link direto para a listagem.
- Para evitar spam, o script só envia uma nova notificação quando aparece um preço **estritamente menor** que o último alertado para aquele item. Quando o menor preço volta a subir acima do alvo, o estado é resetado e o próximo dip dispara de novo.
- O estado de deduplicação fica armazenado em `state.json`, persistido entre runs via cache do GitHub Actions (não é commitado).

## Como criar o seu (fork)

### 1. Fork deste repositório

Clique em **Fork** no topo da página. Use a conta pessoal — repositórios públicos têm minutos de Actions ilimitados e grátis.

### 2. Instale o app ntfy e crie um tópico

- Instale o app **ntfy** (Android: Play Store, iOS: App Store).
- Abra o app e toque em **+** para se inscrever em um novo tópico.
- O nome do tópico funciona como senha — qualquer pessoa que descobrir pode mandar notificações para você. Use algo longo e aleatório, por exemplo: `meutopico-XXXXXXXXXXXXXXXX` (16 caracteres aleatórios).
- Deixe a opção "Use another server" desligada (vamos usar o servidor público `ntfy.sh`).

Para testar, rode em qualquer terminal:
```bash
curl -d "teste" ntfy.sh/SEU-TOPICO-AQUI
```
O celular deve vibrar em ~1 segundo.

### 3. Configure o secret `NTFY_TOPIC` no seu fork

O nome do tópico ntfy precisa ficar guardado como **secret** do GitHub Actions — assim ele fica disponível para o workflow mas não aparece no código (que é público) nem nos logs.

Passo a passo:

1. Na página do seu fork no GitHub, clique na aba **Settings** (canto superior direito da barra do repositório).
2. No menu lateral esquerdo, vá em **Secrets and variables** e clique em **Actions**.
3. Clique no botão verde **New repository secret**.
4. Preencha:
   - **Name**: `NTFY_TOPIC` (exatamente assim, em maiúsculas, sem espaços).
   - **Secret**: cole o nome do tópico que você escolheu no passo 2 — **apenas o nome**, sem `https://ntfy.sh/` e sem barras. Ex: `meutopico-abc123def456ghij`.
5. Clique em **Add secret**.

Pronto. O workflow vai injetar esse valor como variável de ambiente `NTFY_TOPIC` quando rodar o `watch.py`. O secret nunca fica visível depois de salvo (nem para você) — se precisar mudar o tópico, é só clicar em **Update** ao lado de `NTFY_TOPIC` e colar o novo valor.

> **Atenção**: se você esquecer de configurar esse secret, o workflow ainda roda e imprime os preços nos logs, mas avisa `WARN: NTFY_TOPIC not set — running in dry-run mode (no notifications)` e nenhum push é enviado.

### 4. Edite `config.yaml`

```yaml
server: FREYA

items:
  - name: "Ovo de Kiel-D-01"
    search: "Kiel"
    item_id: 9126
    target_price: 30000000
```

Campos:
- **`name`**: rótulo amigável usado nos logs e na notificação.
- **`search`**: termo de busca, equivalente ao que você digitaria na caixa de pesquisa do shop-search.
- **`item_id`**: ID do item no catálogo. Necessário porque uma busca pode retornar itens variados (ex: buscar "Kiel" pode retornar cartas, ovos, etc.) — o `item_id` filtra para o item exato.
- **`target_price`**: o alerta dispara quando o menor preço de COMPRA for menor ou igual a esse valor (em zeny, sem pontos).

**Como descobrir o `item_id`:** abra a página de shop-search no navegador (ex: `https://ro.gnjoylatam.com/pt/intro/shop-search/trading?storeType=BUY&serverType=FREYA&searchWord=NOME`), encontre o card do item desejado, e inspecione o elemento `<li class="card_shop_card__...">`. O atributo `data-id` é o `item_id`.

Servidor é global (campo `server`). Valores válidos: `FREYA`, `NIDHOGG` — use o nome exato que aparece no seletor da página.

### 5. Habilite Actions no fork (se necessário)

Forks às vezes vêm com Actions desabilitado. Vá em **Actions** no fork — se aparecer um aviso, clique para habilitar workflows.

### 6. Dispare um run de teste

Vá em **Actions → price-watch → Run workflow → Run workflow**.

Verifique os logs do job — você deve ver algo como:
```
[Ovo de Kiel-D-01] 5 listings, lowest=41,000,000z (seller='CelsaRussomana', trade='@quit') target<=30,000,000z
[Familiar de Combate] 6 listings, lowest=27,950,000z (...) target<=20,000,000z
```

Para testar a notificação, baixe temporariamente o `target_price` no `config.yaml` para um valor acima do preço atual (ex: `50000000`), faça commit, rode o workflow novamente, e confirme que o celular recebeu a push. Depois reverta o valor.

## Custos

- **GitHub Actions**: repositório público = minutos ilimitados e grátis no plano gratuito.
- **ntfy.sh** (servidor público): grátis para uso pessoal.

## Limitações

- O cron do GitHub Actions é **best-effort** — pode atrasar 5 a 15 minutos durante períodos de pico, especialmente para schedules muito frequentes. Não conte com sub-minute precision.
- Workflows agendados são **desativados automaticamente após 60 dias sem atividade no repositório**. Qualquer push para o repo reseta o contador.
- O nome do tópico ntfy é, na prática, sua única "senha". Não compartilhe e use um valor aleatório longo. Se preferir autenticação real, considere [self-hostar o ntfy](https://docs.ntfy.sh/install/) ou usar o plano pago.
- O script depende da estrutura HTML atual do shop-search. Se o site mudar o layout, os regex em `watch.py` podem quebrar — basta inspecionar a página nova e ajustar.

## Estrutura do projeto

```
├── .github/workflows/watch.yml   # cron + dispatch manual
├── watch.py                      # script principal
├── config.yaml                   # sua lista de itens
├── requirements.txt              # PyYAML
└── .gitignore                    # state.json é local-only / cache-only
```
