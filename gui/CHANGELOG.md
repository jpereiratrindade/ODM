# Histórico do Paralaxe

Este histórico registra a aplicação independente; o histórico do motor OpenDroneMap
permanece no Git e na documentação original. As funcionalidades abaixo foram
implementadas em 26–27/11/2026. Para instruções e limites, consulte o [README](README.md).

## 2026-11-27 — Identidade Paralaxe

- Nome próprio, símbolo com duas perspectivas convergentes, favicon, ícones SVG,
  imagens de aplicativo e manifesto do navegador.
- Identidade na navegação, produtos, relatórios JSON, exportações ZIP e documentação.
- OpenDroneMap identificado como motor, preservando atribuições e licença AGPL v3.
- Comando `pixi run paralaxe`; `pixi run gui` permanece compatível.
- Usuário de rede `paralaxe`, com alias legado `odm` e a mesma senha configurada.
- Nomes técnicos, formatos, projetos existentes e repositório ODM preservados.

## 2026-11-26–27 — Preparação, análise e processamento

- Interface em português para configurar produtos, acompanhar logs reais,
  interromper e inspecionar resultados com motor nativo ou imagem local Podman.
- Ortofotos, elevações, nuvens, modelos e relatórios com prévias e downloads.
- Limpeza reversível de intermediários com revisão, manifesto e restauração,
  preservando originais e arquivos de apoio.
- Galeria de fotos e vídeos, metadados e planejamento de GSD em WebAssembly.
- Acompanhamento de containers Podman externos reconhecidos pelo projeto montado.
- Análise independente de imagens e vídeos, movimento entre quadros e indícios
  geométricos; inspeção de componentes e pontos extremos de reconstrução existente.
- Extração configurável para projeto novo, com diagnóstico dos quadros aceitos
  e descartados, preservação do vídeo e cancelamento.
- Seletores de pasta independentes nas ferramentas de consulta e análise.
- Acesso pela rede local com senha, hosts explícitos e controles de sessão/origem.
- Análise e extração no navegador com OpenCV.js/WASM, seleção de fotos,
  exportação JSON/ZIP ou pasta local quando suportada e envio explícito de fotos
  para projeto novo. A reconstrução continua no servidor.
- Testes de servidor, limpeza, prévias, triagem, Worker WASM e integração Firefox.

Os diagnósticos são heurísticos e não certificam paralaxe 3D, sobreposição,
acurácia cartográfica ou sucesso da reconstrução. Veja os limites de cada modo
no README. Dados privados e resultados dos levantamentos não integram o código.
