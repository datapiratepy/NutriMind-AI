# Architecture Diagrams

Mermaid sources — GitHub renders these natively. Reused by the README,
presentation and About page.

## 1. Overall system

```mermaid
flowchart TB
    UI["Browser<br/>Bootstrap 5 · SSE chat · vanilla JS"]
    API["Flask API<br/>blueprints · validation · request IDs"]
    CO["Coordinator Agent<br/>hybrid router"]
    KA["Knowledge<br/>Agent"]
    MP["Meal Planner<br/>Agent"]
    MA["Meal Analyzer<br/>Agent"]
    HA["Health Advisor<br/>Agent"]
    LLM["LLMClient<br/>watsonx.ai Granite ⇄ demo engine"]
    RET["Retriever + Ingestion<br/>chunk · embed · threshold"]
    DET["Deterministic services<br/>targets · food table · BMI · score"]
    VS[("ChromaDB")]
    DB[("SQLite")]

    UI --> API --> CO
    CO --> KA & MP & MA & HA
    KA & HA --> RET --> VS
    MP & MA --> DET
    KA & MP & MA & HA --> LLM
    API --> DB
```

## 2. Agent routing

```mermaid
flowchart TD
    M["User message"] --> R{"Rule stage<br/>ordered regex · 0 tokens"}
    R -->|small talk / BMI| C["Coordinator answers directly<br/>deterministic · 0 tokens"]
    R -->|clear intent| A["Specialist agent"]
    R -->|no match| L{"Granite JSON classification<br/>~60 tokens · live mode only"}
    L -->|valid| A
    L -->|invalid / demo| D["Default: Knowledge Agent"]
    D --> A
    A --> X["Response + routing explanation<br/>(intent · agent · reason · method)"]
    C --> X
```

## 3. RAG pipeline

```mermaid
flowchart LR
    subgraph Ingestion
        P["PDF upload"] --> V["validate + SHA-256 dedup"]
        V --> E["extract text per page (pypdf)"]
        E --> CH["chunk ~800 chars<br/>page-bounded · 120 overlap"]
        CH --> EM["embed<br/>granite-embedding / local / hash"]
        EM --> IX[("ChromaDB<br/>kb_&lt;provider&gt;")]
    end
    subgraph Query
        Q["question"] --> QE["embed query"] --> S["top-5 similarity search"]
        IX -.-> S
        S --> T{"similarity ≥ 0.35?"}
        T -->|yes| G["grounded prompt → Granite<br/>+ [n] citations (file · page)"]
        T -->|no| N["general knowledge<br/>explicitly labeled"]
    end
```

## 4. Database

```mermaid
erDiagram
    USER_PROFILE {
        int id PK
        string name
        int age
        float height_cm
        float weight_kg
        json medical_conditions
        json allergies
        string food_preference
        string weight_goal
    }
    MEAL_LOGS {
        int id PK
        datetime ts
        json items
        float calories
        float protein_g
        int quality_score
    }
    MEAL_PLANS {
        int id PK
        string title
        json targets
        json plan
    }
    CHAT_MESSAGES {
        int id PK
        string session_id
        string role
        string agent
        boolean rag_used
        json sources
        int tokens_used
    }
    DOCUMENTS {
        int id PK
        string filename
        string sha256
        string status
        int chunk_count
    }
    BMI_RECORDS {
        int id PK
        float bmi
        string category
    }
    WATER_LOGS {
        int id PK
        date date
        int glasses
    }
```

*Single-profile deployment: tables are related through application logic
rather than foreign keys; the multi-user migration path adds `profile_id`
FKs (IMPLEMENTATION_NOTES.md).*

## 5. Request flow (any API call)

```mermaid
sequenceDiagram
    participant B as Browser
    participant M as Middleware
    participant R as Route
    participant V as Validators
    participant S as Service
    participant D as SQLite

    B->>M: HTTP request
    M->>M: assign request ID · start timer
    M->>R: dispatch
    R->>V: validate input
    alt invalid
        V-->>B: 400 {error, code, hint, request_id}
    else valid
        R->>S: business logic
        S->>D: query / persist
        S-->>R: result
        R-->>B: JSON + X-Request-ID
    end
    M->>M: access log (method, path, status, ms)
```

## 6. Chat flow (SSE)

```mermaid
sequenceDiagram
    participant U as Browser (chat.js)
    participant C as /api/chat (SSE)
    participant CO as Coordinator
    participant A as Specialist agent
    participant T as Toolbox
    participant G as Granite / demo

    U->>C: POST {message, session_id}
    C-->>U: event status "Analyzing…"
    C->>CO: route(message)
    C-->>U: event routing {intent, agent, reason, method}
    CO->>A: run(request)
    A->>T: retrieve / calculate / lookup
    A-->>U: event status "Searching knowledge…"
    A->>G: grounded prompt
    loop generation
        G-->>A: token
        A-->>U: event token {text}
    end
    A-->>C: final (text + metadata)
    C->>C: persist user + assistant turns
    C-->>U: event final {message_id, meta}
```
