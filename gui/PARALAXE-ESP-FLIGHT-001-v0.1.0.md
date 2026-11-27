# PARALAXE-ESP-FLIGHT-001 — v0.1.0

**Especificação Técnico-Científica e Plano Diretor de Planejamento, Aquisição e Avaliação Fotogramétrica**

- **Documento:** `PARALAXE-ESP-FLIGHT-001-v0.1.0.md`
- **Branch:** `feat/local-gui`
- **Referência de base:** commit `b0cd6260` (e alterações locais da GUI em `gui/`)
- **Escopo:** pasta `gui/` (nenhuma alteração no núcleo fotogramétrico do ODM em `opendm/` ou `stages/`)
- **Equipamento-alvo inicial:** DJI Mini 4 Pro + DJI RC 2 (adaptador isolado)

---

## 1. Princípios Arquiteturais

1. **Separação em três camadas:**
   - **Científica e geométrica:** modelo físico de câmera, GSD horizontal/vertical, footprint, sobreposição, cadência, borramento por movimento (*motion blur*) e cobertura.
   - **Operacional:** autonomia conservadora (`20–25 min`), limite de waypoints (`200` no DJI Fly), área de estudo vs área autorizada de voo, referência vertical explícita e alertas regulatórios (SARPAS / teto de `120 m`).
   - **Adaptador de equipamento:** tradução isolada para formato proprietário (`.kmz` WPML em `gui/flight_dji.py`), permitindo suporte futuro a outros drones sem alterar o motor.
2. **Offline-first e zero dependências externas obrigatórias:** cálculo geométrico e geração de missões usam apenas a biblioteca padrão do Python (`math`, `json`, `zipfile`, `xml.etree.ElementTree`). Leitura de metadados (`preview.py`) e análise de imagens (`preflight.py`) continuam usando `Pillow`/container conforme já implementado.
3. **Independência entre ferramentas (`sources.js` / `tool_context`):** o planejador deve operar em 4 estados de projeto:
   - Sem projeto (elaboração livre de missão);
   - Projeto somente com planejamento (`flight_plan/mission.json`, sem `images/`);
   - Projeto com fotografias (`images/`, apto à análise e ao ODM);
   - Projeto processado (apto à comparação Planejado × Adquirido × Reconstruído).
4. **Reaproveitamento da base existente:**
   - `gui/preview.py` (`planning_metadata`) e `POST /api/planning` para extração de metadados EXIF/XMP;
   - `gui/web/planning.js` e `gui/web/worker.js` (`/wasm/gsd.wasm`) na interface;
   - `gui/preflight.py` para análise pós-voo;
   - `gui/cleanup.py` preservando `flight_plan/` intacto na limpeza reversível.

---

## 2. Contrato Físico, Matemático e de Referência (`FLIGHT-PATCH-001`)

### 2.1 Modelo de Câmera
Cada perfil explícita:
- `profile_id`, `name`, `width_px`, `height_px`, `sensor_width_mm`, `sensor_height_mm`, `focal_mm`, `min_interval_s`;
- `sensor_source` e `uncertainty_note` (metadados EXIF ≠ calibração óptica geométrica).

Presets iniciais do **DJI Mini 4 Pro** (sensor `1/1.3"` CMOS, lente `6.72 mm` real / `~24 mm` eq., FOV diagonal nominal `82.1°`):
- `dji-mini-4-pro-12mp`: `4000 × 3000 px`, intervalo mínimo nominal `2.0 s`;
- `dji-mini-4-pro-48mp`: `8000 × 6000 px` (ou `8064 × 6048 px` quando lido de EXIF), intervalo mínimo nominal `5.0 s`.

### 2.2 Equações Fundamentais (Terreno Plano, Câmera Nadiral)
Sejam `H > 0` (altura em metros na referência vertical declarada), `s_w, s_h > 0` (sensor em mm), `f > 0` (focal em mm) e `W, H_px > 0` (dimensões em pixels):

- **GSD horizontal (cm/px)** — idêntico ao `/wasm/gsd.wasm`:
  $$\text{GSD}_h = \frac{H \cdot s_w}{f \cdot W} \cdot 100$$
- **GSD vertical (cm/px):**
  $$\text{GSD}_v = \frac{H \cdot s_h}{f \cdot H_{\text{px}}} \cdot 100$$
- **GSD nominal:** $\text{GSD} = \max(\text{GSD}_h, \text{GSD}_v)$
- **Footprint no solo (m):**
  $$F_w = \frac{H \cdot s_w}{f}, \qquad F_h = \frac{H \cdot s_h}{f}$$
- **Espaçamento entre faixas e entre fotos (m)** para sobreposição lateral $o_s \in (0, 1)$ e frontal $o_f \in (0, 1)$:
  $$d_{\text{faixa}} = F_w \cdot (1 - o_s), \qquad d_{\text{foto}} = F_h \cdot (1 - o_f)$$
- **Cadência e velocidade máxima:**
  - Intervalo requerido na velocidade $v$: $\Delta t_{\text{req}} = d_{\text{foto}} / v$
  - Velocidade máxima por cadência mínima $\Delta t_{\min}$: $v_{\max,\text{cad}} = d_{\text{foto}} / \Delta t_{\min}$
  - Velocidade máxima por borramento (*motion blur*), quando informados tempo de exposição $t_{\text{exp}}$ (s) e limite $b_{\max}$ (px, padrão `1.0 px`):
    $$v_{\max,\text{blur}} = \frac{b_{\max} \cdot (\text{GSD} / 100)}{t_{\text{exp}}}$$
  - Velocidade máxima efetiva: $v_{\max} = \min(v_{\max,\text{cad}}, v_{\max,\text{blur}})$ (se $t_{\text{exp}}$ fornecido).
- **Planejamento inverso:** dado $\text{GSD}_{\text{alvo}}$ (cm/px), a altura necessária é:
  $$H = \frac{\text{GSD}_{\text{alvo}} \cdot f \cdot W}{100 \cdot s_w}$$
  (usando o eixo mais restritivo para garantir $\max(\text{GSD}_h, \text{GSD}_v) \le \text{GSD}_{\text{alvo}}$).

### 2.3 Referência Vertical e CRS
- **Referência vertical explícita:** `relative_to_takeoff` (padrão DJI Fly), `agl` (somente com DEM em `PATCH-010`) ou `msl`. Nunca tratar `relative_to_takeoff` como AGL real em terreno ondulado sem aviso explícito.
- **CRS:** geometria persistida em `EPSG:4326` (WGS84). Projeção métrica local no plano tangente ao centroide da área de estudo, válida para levantamentos locais de pequeno/médio porte.

---

## 3. Modelo de Dados da Missão (`mission.json`)

```text
<projeto>/
├── flight_plan/
│   └── <campanha>/
│       ├── mission.json            # registro principal versionado
│       ├── study_area.geojson
│       ├── flight_area.geojson
│       ├── planned_route.geojson
│       ├── mission.kml
│       ├── mission.kmz
│       └── acquisition_report.json
├── images/
└── ... (produtos ODM)
```

O `mission.json` registra: `schema_version`, `mission_id`, `created_at`, `camera` (perfil + proveniência + incerteza), `vertical_reference`, `crs`, `parameters` (direto/inverso, sobreposições, velocidade, cadência), `estimates` (GSD, footprint, fotos, tempo, baterias), `warnings` e `compatibility` (`V0`–`V6`).

---

## 4. Níveis de Validação DJI (`V0`–`V6`)

| Nível | Evidência exigida |
| :--- | :--- |
| `V0` — Estrutural | Arquivo `.kmz` íntegro e XML válido |
| `V1` — Formato | Estrutura compatível com `.kmz` nativo do DJI RC 2 |
| `V2` — Importação | Missão reconhecida no DJI Fly |
| `V3` — Inspeção | Waypoints, altitude, velocidade e gimbal exibidos corretamente |
| `V4` — Execução | Trajetória executada em campo |
| `V5` — Aquisição | Fotos capturadas conforme cadência e parâmetros esperados |
| `V6` — Resultado | Conjunto adequado à reconstrução fotogramétrica no ODM |

---

## 5. Plano Diretor e Checklist de Entregas

- **Marco 000 — Fundamentação**
  - [x] `DOC-001`: Especificação versionada (`PARALAXE-ESP-FLIGHT-001-v0.1.0.md`)
  - [x] Contrato matemático, vertical e de referência definido
  - [x] Modelo de dados `mission.json` definido
- **Marco 001 — Planejador Científico (Nadiral)**
  - [x] `FLIGHT-PATCH-001`: `gui/flight_plan.py` (câmera, GSD H/V, direto/inverso, cadência, blur, equivalência WASM × Python)
  - [x] `FLIGHT-PATCH-002A`: Geometria nadiral simples (retângulos/convexos, terreno plano, projeção local, faixas)
  - [x] `FLIGHT-PATCH-003`: Interface autônoma em `planning.js` (sem exigir projeto/pasta/fotos)
  - [x] `FLIGHT-PATCH-005A`: Persistência `mission.json` e exportação `KML` / `GeoJSON`
- **Marco 002 — Integração DJI (Trilha Paralela)**
  - [ ] `FLIGHT-EXP-001`: Análise de `.kmz` nativo extraído do DJI RC 2 (`gui/fixtures/dji/`)
  - [ ] `FLIGHT-PATCH-004`: Validação operacional, autonomia (`20–25 min`) e divisão por `200` waypoints
  - [ ] `FLIGHT-PATCH-006`: Adaptador `gui/flight_dji.py` (condicionado ao `EXP-001`)
  - [ ] `FLIGHT-EXP-002`: Validação em campo (níveis `V2`–`V6`)
- **Marco 003 — Avaliação Fotogramétrica (3 Níveis)**
  - [ ] `FLIGHT-PATCH-007`: Planejado × Adquirido (`gui/flight_analysis.py` + `gui/preflight.py`)
  - [ ] `FLIGHT-PATCH-008`: Comparação com o Reconstruído (ODM)
- **Marco 004 — Planejamento Avançado**
  - [ ] `FLIGHT-PATCH-002B`: Polígonos côncavos, buracos, área autorizada vs estudo, grade dupla e oblíquas
  - [ ] `FLIGHT-PATCH-005B`: Persistência de missões avançadas
- **Marco 005 — Instrumentação Científica**
  - [ ] `FLIGHT-PATCH-009A`: Campanhas temporais (grade com origem fixa e histórico)
  - [ ] `FLIGHT-PATCH-009B`: Importação GNSS L1/L2
  - [ ] `FLIGHT-PATCH-009C`: Marcação visual em pixel e geração de `gcp_list.txt`
  - [ ] `FLIGHT-PATCH-009D`: Avaliação independente com pontos de verificação (CP)
- **Marco 006 — Terreno**
  - [ ] `FLIGHT-PATCH-010`: Planejamento considerando DEM e referências verticais
- **Linha Independente — Desempenho da GUI**
  - [ ] `PATCH-CONC`: Diagnóstico/benchmark e concorrência limitada para miniaturas e prévias (`media_lock` / `preview_lock` em `gui/server.py`)
