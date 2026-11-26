# Interface experimental ODM

Interface local em português, inspirada na estrutura e nas cores do
[SITUS](https://github.com/jpereiratrindade/SITUS/tree/main/web): menu lateral,
painéis claros, azul e teal, SVG local e ausência de CDN ou telemetria.
Protótipo independente da CLI existente, sem dependências Python adicionais.

## Origem, licença e transparência

Esta interface é uma modificação independente do
[OpenDroneMap/ODM](https://github.com/OpenDroneMap/ODM), mantida por José Pedro
Trindade ([jpereiratrindade](https://github.com/jpereiratrindade)). Modificações
realizadas em **26/11/2026**, com desenvolvimento assistido por IA (Codex).
Não é um lançamento oficial do OpenDroneMap e não implica aprovação dos autores.
O código original e seus créditos permanecem atribuídos ao OpenDroneMap e seus
contribuidores. A referência visual é o projeto SITUS, indicado acima.

O fork e as adições da interface são disponibilizados sob a
[GNU Affero General Public License v3](../LICENSE), sem garantia, conforme a
licença. Código e histórico das modificações estão na
[branch pública feat/local-gui](https://github.com/jpereiratrindade/ODM/tree/feat/local-gui).
A interface oferece links visíveis para licença, código-fonte e projeto original.

As [regras de contribuição do OpenDroneMap](https://github.com/OpenDroneMap/documents/blob/main/CONTRIBUTING.md)
desencorajam uso de IA e proíbem pull requests dirigidos por prompts. Este trabalho
é mantido como experimento independente no fork; não deve ser apresentado como
contribuição aceita ou encaminhado automaticamente como PR ao projeto original.
Uma contribuição futura exige verificar e respeitar a política vigente dos
mantenedores, inclusive divulgação e discussão do uso assistivo de IA quando
admissível.

### Validação desta versão

- Dezessete testes: quatorze de contrato do servidor, incluindo rotas, validação de entrada,
  restrição de origem, montagem Podman, interrupção, diagnóstico persistido,
  cache de prévias, inventário de mídia, requisições parciais de vídeo e proteção
  dos arquivos para download; três de prévias, verificando limites de resolução,
  nodata, amostragem, orientação EXIF e preservação dos originais.
- Interface verificada em navegador Chromium em larguras de 390, 768 e 1440 px;
  cálculo WASM e apresentação de erros de ambiente também verificados.
- Processamento real com ODM 3.6.2 na imagem local Podman, sobre cópia separada
  de 23 JPG: ortofoto GeoTIFF, nuvem LAZ e relatório PDF gerados com sucesso.
  A ortofoto foi aberta com GDAL: 8815 × 10188 pixels, EPSG:32722 e resolução
  aproximada de 5 cm/pixel. Isso valida execução e formato; não certifica acurácia
  cartográfica. Imagens e resultados de teste não fazem parte do repositório.
- Interrupção real de um container de teste verificada, sem deixar processo ativo.

Os testes descritos não equivalem à validação completa do núcleo ODM, de todas as
plataformas ou de todas as combinações de produtos.

## Executar

Suba primeiro o servidor, sem indicar dataset:

```bash
python3 gui/server.py
# Ou, no ambiente Pixi:
pixi run gui
```

Abra **http://127.0.0.1:8097**. Em **Meu levantamento**, clique em **Escolher pasta…**,
navegue até o projeto e clique em **Usar esta pasta**. Também pode informar
um caminho em **Informar localização manualmente**. Pode indicar o projeto contendo `images/` ou a pasta que
contém vários projetos. Nenhum arquivo é copiado ou enviado pelo navegador.
A pasta pode ser trocada pela interface quando não houver execução ativa.

Organize os levantamentos assim:

```text
/caminho/datasets/
  levantamento-01/
    images/
      foto-001.jpg
      foto-002.jpg
```

Opcionalmente, indique uma pasta inicial ao subir o servidor, usando o mesmo
ambiente Python configurado para ODM:

```bash
pixi run gui --datasets /caminho/datasets
# Ou, com ambiente ODM já ativado:
python gui/server.py --datasets /caminho/datasets
```

Abra **http://127.0.0.1:8097**. Porta alternativa: `--port 8098`.
O servidor também abre sem as dependências de fotogrametria; executar um
levantamento exige um motor ODM funcional. Na inicialização, `--engine auto`
(padrão) usa a instalação nativa quando os binários principais estão presentes;
caso contrário, procura uma imagem ODM já instalada no Podman. Não baixa imagens.

```bash
# Usar a imagem ODM instalada no Podman:
python3 gui/server.py --engine podman
# Usar o ambiente nativo com as dependências e binários compilados:
python gui/server.py --engine native
```

A imagem padrão é `docker.io/opendronemap/odm:latest`; `--image` aceita outra
imagem local, inclusive uma versão fixada. Esse motor executa o código contido
na imagem, que pode ter versão diferente do checkout deste repositório. A GUI
continua sendo a versão local. Para executar alterações locais do núcleo ODM,
é necessário usar o ambiente nativo ou construir uma imagem com essas alterações.

No Podman, somente o projeto selecionado é montado no container, com identidade
do usuário atual, rede desativada e sem alteração de rótulos SELinux nos arquivos.
A opção Interromper para o container; não deixa o processamento destacado.
O seletor também aceita um caminho terminado em `images/`, usando seu projeto pai.
Erros de ambiente e de acesso aparecem em uma mensagem visível, além dos logs.

As cinco seções têm telas próprias: **Meu levantamento**, **Planejar um voo**,
**Meus produtos**, **Visualizar resultados** e **Fotos e vídeos**. Escolha um perfil visual (rápido, equilibrado ou detalhado),
marque os produtos desejados e clique **Criar ortofoto e produtos**. A ortofoto e
a nuvem de pontos estão incluídas; os demais cartões adicionam produtos. O acompanhamento
mostra grupos de etapas identificadas na saída real do ODM, sem percentual estimado.
O planejamento inclui um esquema do voo e os resultados são apresentados em
cartões, com indicação de quais arquivos existem no projeto. As ilustrações dos
cartões representam tipos de produto, não são prévias dos arquivos.

**Detalhes técnicos e diagnóstico**, recolhido por padrão, contém logs e o
botão **Mostrar comando ODM**, com os argumentos exatos como strings entre aspas.
O processamento chama `run.py` com o Python do servidor no motor nativo, ou o
entrypoint da imagem no motor Podman, sempre sem shell.
Há uma execução por servidor, logs reais com atualização a cada 1,5 segundo e
interrupção do grupo de processos em Linux/macOS. Em Windows, a interrupção
atinge o processo Python principal; processos nativos filhos podem continuar.

O ODM grava resultados no projeto e pode retomar etapas existentes segundo suas
regras habituais. O inventário mostra produtos conhecidos e abre o visualizador.
Não há upload, mapa-base externo ou gerenciador persistente de filas.
Logs da sessão ficam em memória, com limite de 2.000 linhas. O resumo do último
processamento é recuperado do `log.json` do projeto após reiniciar o servidor. Fechar
a aba não interrompe a execução; encerrar o servidor com Ctrl+C solicita parada.

## Visualizar resultados

Em **Visualizar resultados**, escolha o levantamento e o produto e clique
**Visualizar**. O botão de cada cartão em **Meus produtos** também abre a prévia.

- **Ortofoto, DSM e DTM:** PNG gerado com GDAL a partir do raster real, reduzido
  para no máximo 1.600 pixels no maior lado, com zoom, pan e metadados de dimensão,
  CRS e tamanho de pixel. Elevações são exibidas em escala de cinza, ajustada à
  faixa da prévia. Não é mapa de consulta cartográfica nem ferramenta de medição.
- **Nuvem LAZ:** PDAL lê amostra de até 12.000 pontos, exibida em projeção 3D
  ortográfica, com rotação por arraste e cores por altura relativa. A amostra
  não substitui a nuvem original para análise ou medição.
- **Modelo OBJ:** amostra dos vértices reais, com rotação; sem faces ou texturas.
  O download do OBJ não inclui os arquivos MTL ou texturas acompanhantes.
- **Relatório PDF:** aberto em painel próprio, usando o leitor PDF do navegador.
- **Baixar arquivo original:** transferência do produto conhecido sem reduzir
  resolução. Se o navegador não exibir o PDF, o download continua disponível.

Os downloads exigem sessão local e ficam limitados aos produtos conhecidos no
projeto; caminhos arbitrários e links simbólicos para fora do projeto são
rejeitados. O servidor transfere arquivos em blocos. Prévias são calculadas no
motor configurado, com montagem somente para leitura no Podman, timeout de 60
segundos e cache de até seis produtos, invalidado por tamanho/data de alteração.
No modo nativo, GDAL e PDAL precisam estar disponíveis no Python do servidor.

### Diagnóstico do perfil rápido

O perfil **Explorar rápido** reduz qualidade de extração e densificação. No
levantamento de teste, o log registrou reconstrução densa com zero pontos, seguida
de erro de segmentação (139) no filtro OpenMVS. A execução que funcionou usou o
perfil **Equilíbrio**. Isso não demonstra corrupção das imagens.

A interface traduz esse caso, remove códigos de cor ANSI das mensagens e mostra
perfil/versão do último processamento. Ao mudar o perfil após uma falha, adiciona
`--rerun-from opensfm` para regenerar intermediários: uma execução malsucedida
pode não ter salvo `options.json`, necessário à detecção habitual de mudanças.
As imagens originais são preservadas.

## Inspecionar fotos e vídeos

Na aba **Fotos e vídeos**, escolha o levantamento para consultar os arquivos
diretamente na pasta `images/`. Há busca por nome, filtro por tipo, páginas de
24 itens e miniaturas carregadas sob demanda. A inspeção é somente leitura:
não exclui arquivos nem altera a seleção de fotos do processamento.

Clique em uma foto para abrir a prévia ampliada, alternar entre arquivos,
consultar tamanho, dimensões e metadados EXIF de captura quando existentes
(câmera, data, exposição, abertura, focal e ISO). A orientação EXIF é aplicada
à prévia. Miniaturas têm até 360 pixels e prévias de inspeção até 2.400 pixels;
Pillow é usado no servidor quando disponível, ou na imagem Podman como fallback.
Formatos RAW/TIFF dependem dos decodificadores instalados; se não houver prévia,
o original continua disponível para download.

Vídeos são reproduzidos com controles nativos do navegador, sem autoplay, com
suporte a requisições HTTP Range para avançar e retroceder. O formato/codec deve
ser compatível com o navegador: não há transcodificação automática. Ao fechar
a inspeção ou trocar de arquivo, a reprodução e o carregamento são interrompidos.
Dimensões e duração aparecem quando o navegador conseguir ler os metadados.

Somente mídias reconhecidas dentro da pasta de imagens são servidas, com sessão
local autenticada; caminhos externos e links simbólicos para fora do projeto são
rejeitados. O cache em memória retém até 48 prévias e respeita alterações de
tamanho/data dos arquivos. Download transmite o arquivo original em blocos.
Galeria, filtros, inspeção e reprodução/seek de um WebM sintético foram validados
em Chromium, incluindo larguras de 390, 768 e 1440 pixels.

## WebAssembly

O cálculo de GSD roda de fato em WebAssembly dentro de um Web Worker. O módulo
incluído usa quatro parâmetros `f64`, sem memória ou imports. A fórmula é:

```text
GSD (cm/pixel) = altura (m) × largura sensor (mm) / focal (mm) / largura imagem (px) × 100
```

É estimativa para câmera nadiral e terreno plano, não promessa de precisão da
ortofoto. A interface informa quando usa fallback JavaScript. A fotogrametria
continua no núcleo nativo: este experimento não porta OpenSfM/OpenMVS/GDAL
para navegador. O objetivo inicial é testar interface e fronteira de execução.

O módulo pode ser reproduzido sem Emscripten; o script inclui o WAT equivalente
e serializa apenas as instruções aritméticas necessárias:

```bash
python gui/build_wasm.py
python -m unittest gui.test_server gui.test_preview -v
node --check gui/web/app.js
node --check gui/web/viewer.js
node --check gui/web/media.js
node --check gui/web/worker.js
node -e "WebAssembly.instantiate(require('fs').readFileSync('gui/web/wasm/gsd.wasm')).then(({instance}) => console.log(instance.exports.gsd(100, 13.2, 8.8, 5472)))"
```

Resultado esperado do exemplo: aproximadamente **2,7412 cm/pixel**.

Servidor restrito a loopback, sem CORS; troca de pasta e início/parada exigem
origem local e token por sessão. A navegação de pastas também exige o token;
permite explorar diretórios acessíveis ao usuário do servidor, sem listar arquivos.
Projetos ficam limitados aos filhos diretos
da pasta de datasets escolhida explicitamente na interface ou na inicialização.
Este servidor experimental destina-se a uma máquina local e um usuário.
