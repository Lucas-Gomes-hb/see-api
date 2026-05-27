# See API

> **Projeto Educacional — Apenas para Uso Não Comercial**
>
> Este projeto foi desenvolvido exclusivamente para fins de aprendizado e estudo. Não deve ser utilizado para nenhuma finalidade comercial, monetização ou lucro de qualquer tipo. Veja a seção [Licença e Termos de Uso](#licença-e-termos-de-uso) para mais detalhes.

---

See API é o backend do aplicativo **See**, um streaming pessoal de filmes e séries. É uma API construída com **FastAPI** que agrega conteúdo de fontes públicas e expõe endpoints padronizados para o app Flutter consumir.

**App Flutter:** [github.com/Lucas-Gomes-hb/see-app](https://github.com/Lucas-Gomes-hb/see-app)

---

## Índice

- [Endpoints](#endpoints)
- [Proxy de Vídeo](#proxy-de-vídeo)
- [Tech Stack](#tech-stack)
- [Como Rodar](#como-rodar)
- [Licença e Termos de Uso](#licença-e-termos-de-uso)

---

## Endpoints

### `GET /`
Health check.

**Resposta:**
```json
{ "status": "online", "service": "See API v4" }
```

---

### `GET /home`
Retorna o feed principal com hero (destaque) e seções de conteúdo. Resultado cacheado por 5 minutos.

**Resposta:**
```json
{
  "hero": [ { "id": "...", "title": "...", "poster": "...", "year": "2024", "rating": 7.5, "media_type": "movie" } ],
  "sections": [
    { "name": "Filmes Populares", "type": "popular", "items": [...] },
    { "name": "Séries", "type": "tv", "items": [...] },
    { "name": "Animes", "type": "anime", "items": [...] }
  ]
}
```

---

### `GET /search?q={query}&page={page}`
Busca filmes e séries.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `q` | `string` | Termo de busca |
| `page` | `int` | Página (padrão: 1) |

**Resposta:**
```json
{
  "results": [ { "id": "...", "title": "...", "poster": "...", "media_type": "movie", "year": "2024", "rating": 8.1 } ],
  "has_more": true,
  "page": 1
}
```

---

### `GET /details/{imdb_id}`
Metadados básicos de um filme ou série via IMDB ID.

**Resposta:**
```json
{
  "id": "tt1234567",
  "title": "...",
  "poster": "...",
  "backdrop": "...",
  "year": "2023",
  "rating": 7.8,
  "media_type": "movie",
  "overview": "...",
  "genres": ["Action", "Drama"],
  "cast": []
}
```

---

### `GET /details/v3/{subject_id}`
Metadados enriquecidos (elenco, dublagens, temporadas, trailer).

**Resposta:**
```json
{
  "id": "...",
  "title": "...",
  "overview": "...",
  "poster": "...",
  "year": "2024",
  "rating": 8.2,
  "media_type": "tv",
  "genres": ["Drama"],
  "runtime": 45,
  "total_seasons": 3,
  "country": "US",
  "subtitles": [...],
  "dubs": [ { "language_code": "pt", "language": "Português", "subject_id": "...", "original": false } ],
  "staff": [ { "name": "...", "character": "...", "staff_type": "actor", "avatar": "..." } ],
  "trailer_url": "https://..."
}
```

---

### `GET /stream/movie/{id}`
Resolve a URL de stream de um filme. Aceita tanto IDs do IMDB (`tt...`) quanto IDs internos numéricos.

**Resposta:**
```json
{
  "url": "https://.../proxy/video?url=...",
  "subject_id": "...",
  "resource_id": "...",
  "qualities": [
    { "resolution": 1080, "url": "https://.../proxy/video?url=..." },
    { "resolution": 720, "url": "https://.../proxy/video?url=..." }
  ]
}
```

---

### `GET /stream/tv/{id}?season={s}&episode={e}`
Resolve a URL de stream de um episódio de série.

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `season` | `int` | Número da temporada (padrão: 1) |
| `episode` | `int` | Número do episódio (padrão: 1) |

**Resposta:** igual ao `/stream/movie`.

---

### `GET /seasons/{subject_id}`
Lista as temporadas de uma série com número de episódios por temporada.

**Resposta:**
```json
{
  "seasons": [
    { "season": 1, "episodes": 10 },
    { "season": 2, "episodes": 8 }
  ]
}
```

---

### `GET /episodes/{subject_id}/{season}`
Lista os episódios de uma temporada com título e duração.

**Resposta:**
```json
{
  "episodes": [
    { "episode": 1, "title": "Pilot", "duration": 45 },
    { "episode": 2, "title": "...", "duration": 42 }
  ]
}
```

---

### `GET /captions/{subject_id}?resource_id={resource_id}`
Retorna as faixas de legenda disponíveis para um conteúdo.

**Resposta:**
```json
{
  "tracks": [
    { "language": "Português", "language_code": "pt", "url": "https://..." },
    { "language": "English", "language_code": "en", "url": "https://..." }
  ]
}
```

---

### `GET /categories`
Lista as categorias/gêneros disponíveis.

**Resposta:**
```json
{
  "results": [
    { "id": "action", "name": "Ação" },
    { "id": "comedy", "name": "Comédia" }
  ]
}
```

---

### `GET /category/{genre_id}`
Retorna até 20 títulos de um gênero específico.

---

### `POST /resolve`
Resolve stream a partir de título e tipo (alternativa ao fluxo por ID).

**Body:**
```json
{ "title": "Inception", "media_type": "movie", "year": "2010" }
```

---

## Proxy de Vídeo

### `GET /proxy/video?url={url}`
Faz proxy da stream de vídeo, adicionando os cabeçalhos necessários (Origin, Referer) e suportando `Range` requests para seek no player. Retorna a stream diretamente ao cliente, sem armazenar o conteúdo.

---

## Tech Stack

| Pacote | Versão | Finalidade |
|---|---|---|
| Python | 3.10+ | Linguagem |
| FastAPI | — | Framework web |
| Uvicorn | — | Servidor ASGI |
| requests | — | HTTP client para proxy |
| moviebox_api | — | Integração com fonte de conteúdo |

---

## Como Rodar

**Pré-requisitos:**
- Python 3.10+
- pip

```bash
# Clonar o repositório
git clone git@github.com:Lucas-Gomes-hb/see-api.git
cd see-api

# Criar e ativar o ambiente virtual
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Instalar dependências
pip install -r requirements.txt

# Rodar o servidor
python main.py
```

O servidor estará disponível em `http://localhost:8001`.
A documentação interativa (Swagger UI) fica em `http://localhost:8001/docs`.

---

## Licença e Termos de Uso

Este projeto é disponibilizado sob a licença **Creative Commons Atribuição-NãoComercial 4.0 Internacional (CC BY-NC 4.0)**.

**Você tem liberdade para:**
- Usar, estudar, copiar, modificar e distribuir este projeto e seu código-fonte.
- Criar trabalhos derivados e compartilhá-los livremente.

**Sob as seguintes condições:**
- **Atribuição** — Você deve dar o crédito apropriado ao(s) autor(es) originais e incluir um link para este repositório.
- **NãoComercial** — Você **não pode** usar este projeto, seu código ou qualquer derivado para fins comerciais, monetização, lucro, serviços pagos ou qualquer atividade que gere receita.

Texto completo: [creativecommons.org/licenses/by-nc/4.0/deed.pt](https://creativecommons.org/licenses/by-nc/4.0/deed.pt)

---

> **Este projeto existe exclusivamente para fins educacionais e de estudo.**
> Os autores não se responsabilizam pelo uso indevido do software por terceiros.
> **Este projeto não deve ser utilizado para fins comerciais ou para geração de receita de qualquer tipo.**
