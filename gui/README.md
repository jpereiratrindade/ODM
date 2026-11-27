# Paralaxe

![Paralaxe — imagens e fotogrametria](web/assets/paralaxe-wordmark.svg)

Aplicação independente para preparar imagens, analisar perspectivas e produzir
levantamentos com o motor **OpenDroneMap**. Reúne análise de fotos e vídeos,
extração de quadros, planejamento, reconstrução e exploração de resultados.
A interface em português funciona no computador do servidor e pela rede local;
análise e extração também podem executar no navegador de quem acessa a página.

**Produto:** Paralaxe. **Motor de reconstrução:** OpenDroneMap.
O repositório permanece como fork de ODM para preservar compatibilidade e histórico.
A marca é própria: duas perspectivas sobrepostas convergem para um ponto comum.
Símbolo, favicon, ícones e manifesto estão em `gui/web/assets` e `gui/web/manifest.webmanifest`.
Os ícones são SVGs locais; não há fontes de ícones, CDN ou telemetria em tempo de execução.

## Origem, licença e transparência

Esta interface é uma modificação independente do
[OpenDroneMap/ODM](https://github.com/OpenDroneMap/ODM), mantida por José Pedro
Trindade ([jpereiratrindade](https://github.com/jpereiratrindade)). Modificações
realizadas em **26–27/11/2026**, com desenvolvimento assistido por IA (Codex).
Não é um lançamento oficial do OpenDroneMap e não implica aprovação dos autores.
O código original e seus créditos permanecem atribuídos ao OpenDroneMap e seus
contribuidores. A identidade visual da aplicação é Paralaxe.

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

- Suíte Python com 40 testes de servidor, prévias, limpeza reversível e análise de imagens.
  No ambiente local, quatro verificações que requerem OpenCV são ignoradas;
  os demais testes passaram. A suíte cobre autenticação, pastas independentes,
  importação explícita de fotos, proteção dos originais e identidade da aplicação.
- Teste do Worker com o OpenCV.js/WebAssembly real: qualidade, movimento,
  ambiguidade de homografia, reset/commit e exportação ZIP.
- Integração real em Firefox com dados sintéticos: extração no navegador,
  ausência de envio automático, envio explícito de JPEGs e cancelamento,
  preservando um processamento ativo independente.
- Interface verificada em navegador Chromium em larguras de 390, 768 e 1440 px;
  cálculo WASM e apresentação de erros de ambiente também verificados.
- Identidade Paralaxe conferida em Firefox nas larguras de 500 e 1440 px,
  com marca, ícones e navegação responsiva.
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
pixi run paralaxe
```

Abra **http://127.0.0.1:8097**. Em **Meu levantamento**, clique em **Escolher pasta…**,
navegue até o projeto e clique em **Usar esta pasta**. Também pode informar
um caminho em **Informar localização manualmente**. Pode indicar o projeto contendo `images/` ou a pasta que
contém vários projetos. Escolher uma pasta do servidor não copia nem envia arquivos pelo navegador.
O envio de fotos locais é uma operação separada e explícita em **Analisar imagens**.
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
pixi run paralaxe --datasets /caminho/datasets
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

As seis seções têm telas próprias: **Meu levantamento**, **Analisar imagens**, **Planejar um voo**,
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
Há importação explícita de fotos selecionadas no navegador para um projeto novo;
não há envio automático de vídeos, mapa-base externo ou gerenciador persistente de filas.
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

## Limpar processamento preservando os originais

Em **Meu levantamento → Limpar processamento → Revisar limpeza e backups**,
confira a lista de gerados e dos itens que permanecerão no lugar. Para executar,
digite exatamente o nome do levantamento e confirme **Limpar processamento com
backup**. Limpeza e restauração são bloqueadas durante qualquer processamento
ativo gerenciado ou detectado pelo servidor. Containers Podman externos reconhecidos
também bloqueiam essas operações; processos nativos externos não são detectados.
Não execute outro ODM sobre a mesma pasta durante a operação.

A operação é **reversível**: move somente uma lista explícita de pastas e arquivos
gerados pelo ODM, incluindo intermediários OpenSfM, malhas, texturas, nuvem,
ortofoto, DEM, relatório e índices de execução. Não usa padrões genéricos de
extensão nem apaga o projeto. `images/`, vídeos, máscaras de entrada, pontos de
controle, arquivos de alinhamento, `cameras.json`, outras calibrações e itens
desconhecidos permanecem no lugar. Links simbólicos de primeiro nível não são
seguidos nem movidos. Arquivos guardados manualmente dentro de uma pasta de
processamento listada são movidos junto, continuando disponíveis no backup.

Os backups ficam em **`.odm-cleanup/<identificador>/`**, na pasta que contém os
projetos, com manifesto, data e identificação do levantamento. São recuperáveis
pela mesma tela mesmo depois de reiniciar o servidor. A pasta de backup é
ignorada pelo Git. **Esta limpeza não libera espaço em disco:** os dados continuam
armazenados no backup, fora do projeto, para permitir recomeçar sem perder os
resultados anteriores.

**Restaurar este backup** recoloca os itens, sem sobrescrever arquivos já existentes.
Uma prévia vale por dez minutos e é invalidada se os gerados mudarem. Falhas de
movimentação tentam reverter os itens já movidos; se o sistema não permitir a
reversão, a mensagem indica onde conferir projeto e backup, sem apagar conteúdo.
Não há exclusão definitiva automática de backups.

Limpeza/restauração foram validadas somente em projetos descartáveis de teste,
incluindo comparação byte a byte das fotos, vídeos, máscaras e arquivos de apoio.
Nenhuma limpeza foi executada automaticamente nos levantamentos reais.

## WebAssembly

### Planejamento com dados do projeto

**Planejar um voo** carrega automaticamente os parâmetros da foto de referência
do levantamento selecionado. Também permite escolher outro levantamento/foto e
recarregar os dados explicitamente, sem mudar o projeto em processamento.
As informações são lidas de EXIF/XMP dos originais, inclusive antes de executar ODM:
câmera, data de captura, dimensões, focal e equivalente de 35 mm, GPS e altura
relativa DJI quando disponíveis. Cada campo indica a origem; valores podem ser
editados manualmente. Não são médias das fotos ou um certificado de calibração.

A largura do sensor é calculada da resolução do plano focal EXIF quando houver
unidades válidas, ou estimada como `36 × focal / focal_equivalente_35mm`. Esta
segunda opção é uma aproximação baseada no EXIF, explicitamente identificada.
Parâmetros não disponíveis ficam em branco, sem substituir por valores fictícios.

**Altitude GPS e altura relativa à decolagem não são altura acima do terreno.**
A altura do cálculo fica para preenchimento manual. O botão **Usar altura
relativa como aproximação** permite adotar explicitamente o valor XMP, com aviso
para revisar a diferença de relevo em relação à decolagem. Não há consulta a
modelo de terreno nem conversão automática de altitude GPS em altura de voo.
Projeto somente com vídeo precisa primeiro de quadros com metadados de câmera.

O fluxo foi validado com foto real DJI FC6310 (8,8 mm, 5472 × 3078, sensor
estimado de 13,2 mm), incluindo GSD, origem dos valores, aproximação de altura,
edição manual e troca para foto sem EXIF sem reaproveitar parâmetros anteriores.

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
python -m unittest gui.test_server gui.test_preview gui.test_cleanup gui.test_preflight -v
node --check gui/web/app.js
node --check gui/web/viewer.js
node --check gui/web/media.js
node --check gui/web/planning.js
node --check gui/web/cleanup.js
node --check gui/web/worker.js
node -e "WebAssembly.instantiate(require('fs').readFileSync('gui/web/wasm/gsd.wasm')).then(({instance}) => console.log(instance.exports.gsd(100, 13.2, 8.8, 5472)))"
```

Resultado esperado do exemplo: aproximadamente **2,7412 cm/pixel**.

Por padrão, servidor restrito a loopback, sem CORS; troca de pasta e início/parada exigem
origem local e token por sessão. A navegação de pastas também exige o token;
permite explorar diretórios acessíveis ao usuário do servidor, sem listar arquivos.
Projetos ficam limitados aos filhos diretos
da pasta de datasets escolhida explicitamente na interface ou na inicialização.
Este servidor experimental usa uma sessão de trabalho compartilhada; não oferece
contas individuais, isolamento entre usuários ou filas para equipes.

### Execuções iniciadas fora da interface

No motor Podman, a interface detecta containers ativos com `--project-path` e montagem do projeto em `/datasets/<nome>`. Ela acompanha os últimos 2.000 registros e os novos logs, identifica a execução como externa e bloqueia limpeza e novo processamento. O botão de interrupção atua no container acompanhado. Após reiniciar a interface, a execução ativa é detectada novamente. A descoberta consulta containers do usuário atual a cada cinco segundos; processos nativos externos ainda não são detectados.

### Verificação antes do processamento

O botão **Verificar fotos e vídeos** faz uma triagem sem alterar originais. Se ainda não houver uma verificação da configuração atual, o primeiro clique para criar produtos mostra o relatório; o segundo inicia. Arquivos amostrados que não podem ser decodificados impedem o início. Avisos de qualidade permanecem informativos.

A análise usa até 32 pares de fotos adjacentes em ordem natural e até 12 pares de quadros por vídeo, nos primeiros quatro vídeos. Nitidez (variância do Laplaciano < 40), exposição (> 60% de pixels quase pretos ou brancos), contraste (desvio padrão < 12) e semelhança (diferença média < 2 em miniaturas 64 × 36) são heurísticas, não um diagnóstico ou probabilidade de falha. A nitidez é avaliada com largura de até 640 pixels. A verificação mede deslocamento visual e consistência de correspondências, mas não mede sobreposição ou paralaxe 3D nem reconhece telas ou movimento da cena.

Quando existe `opensfm/reconstruction.json`, o relatório mostra componentes e pontos não finitos ou além de 100 vezes o raio do percentil 99, medido a partir do centro mediano da componente. É um aviso sobre a reconstrução existente, sem filtrar pontos nem garantir a geometria da próxima execução.

A interface permite escolher limite global de quadros (500), resolução de extração (2.000 px) e paralelismo (8). O intervalo nominal é apenas duração dividida por limite menos um; os filtros do ODM determinam os quadros efetivamente extraídos. Fotos já existentes não são redimensionadas. A verificação usa OpenCV local ou a imagem ODM instalada, com montagem somente para leitura; não modifica os filtros internos de extração do ODM.

### Movimento entre quadros e extração em novo projeto

A triagem compara até 32 pares de fotos vizinhas na ordem natural e até 12 pares por vídeo no trecho escolhido. Os vídeos são testados no intervalo de candidatos configurado, aumentado quando necessário para distribuir o limite pelo trecho inteiro. ORB usa correspondências mútuas com teste de razão 0,75; RANSAC testa homografia (3 px) e matriz fundamental (1,5 px), em imagens de até 800 px. Com menos de 20 correspondências, o resultado é indeterminado. Movimento abaixo de 0,3% da diagonal com resíduo de homografia abaixo de 0,15% sinaliza pouca mudança; homografia com mais de 90% de suporte e baixo resíduo sinaliza ambiguidade entre giro, cena plana e pouca paralaxe. Não é uma estimativa de rotação ou profundidade. Padrões repetidos e objetos móveis podem enganar os modelos.

**Seleção de quadros por movimento** permite escolher início/fim em segundos, intervalo mínimo entre candidatos (1 s), nitidez mínima (40 na imagem de até 640 px), deslocamento mínimo (0,8% da diagonal) e intervalo de segurança (5 s). O botão **Extrair para novo projeto** utiliza esses controles, o limite de fotos e a resolução, sem modificar a extração automática do ODM. Esta última continua usando somente limite e resolução. A extração personalizada não pode começar enquanto há outro processamento ativo na interface.

O destino deve ser um projeto novo. O vídeo é montado somente para leitura e os JPGs são gravados com qualidade 95, sem ampliar a resolução original. O limite distribui candidatos pelo trecho inteiro; filtros podem resultar em menos fotos. Baixa nitidez e mais de 80% de pixels quase pretos/brancos descartam candidatos. O movimento é comparado com o último quadro aceito; após o intervalo de segurança, baixa movimentação pode ser aceita e registrada como `safety_gap`. Esse intervalo não garante um espaçamento máximo, pois não força imagens ruins. Quadros sem correspondências suficientes ou com baixa consistência são preservados para revisão, sem eliminar silenciosamente possíveis ligações entre trechos.

`extraction.json` registra parâmetros, intervalo efetivo, índices, tempos, métricas e motivos de aceitação/descarte. Uma extração cancelada pode deixar um projeto parcial, com manifesto incompleto. Menos de três fotos gera falha com os arquivos preservados. A triagem lê esse diagnóstico ao analisar o novo projeto. A extração não inicia a reconstrução e não transfere metadados GPS/SRT: mantenha os vídeos e arquivos auxiliares originais.

### Ferramentas de análise no frontend

A seção independente **Analisar imagens**, acessível pelo menu como **Planejar um voo**, reúne diagnóstico e extração com seleção do levantamento. Essas ferramentas não aparecem dentro de **Meu levantamento**. Ao iniciar sem uma verificação atual, a interface abre a seção de análise para revisão; o relatório oferece um link para voltar à configuração do processamento. Diagnóstico e extração são apresentados em cartões separados. O relatório distingue impedimentos de avisos, mostra a contagem das amostras e recebe foco visual após a análise. Intervalo, nitidez e movimento continuam disponíveis em **Configurar seleção por movimento**. O paralelismo fica na configuração do processamento. A interface usa textos auxiliares maiores, bordas de campos mais visíveis e estados selecionados com preenchimento e contorno reforçados.

### Pastas independentes nas ferramentas

**Analisar imagens**, **Planejar um voo**, **Fotos e vídeos**, **Meus produtos** e **Visualizar resultados** têm um seletor próprio **Escolher outra pasta…**. Escolha a pasta de um projeto com `images/`. A escolha vale para aquela ferramenta, inclusive metadados, miniaturas, vídeos, PDFs e downloads, sem mudar a pasta global, o comando ou o processo ativo. **Usar pasta do processamento** retorna à origem global. As escolhas ficam na sessão da interface; ao atualizar a página, selecione novamente se necessário.

Consultas podem ser feitas durante um processamento. As leituras usam contextos separados, identificados por referências de sessão e protegidos pelas mesmas verificações de caminho e autenticação. A extração continua sendo uma tarefa de gravação: aguarda o processamento terminar e salva o novo projeto na pasta global de projetos. Se ela ainda não estiver configurada, a interface pede uma pasta de saída. Escolher uma origem de ferramenta nunca autoriza limpeza ou troca do projeto em processamento.

### Acesso pela rede local

O padrão continua sendo `127.0.0.1`. Para escutar em IPv4 na rede local, use `--host 0.0.0.0`, informe os endereços aceitos com `--allowed-host` e indique um arquivo de senha com `--lan-password-file`. O arquivo deve conter uma senha de pelo menos 12 caracteres. O modo de rede recusa iniciar sem senha e sem endereços explícitos.

```bash
python3 gui/server.py --datasets ~/odm --host 0.0.0.0 \
  --allowed-host 10.163.80.176 --allowed-host 10.163.4.160 \
  --lan-password-file ~/.config/paralaxe/lan-password
```

O navegador solicita usuário **paralaxe** e a senha do arquivo, inclusive no acesso por localhost quando este modo está ativo. Todas as páginas, arquivos e APIs exigem essa autenticação. Os controles de sessão e origem continuam ativos; o Host deve estar na lista configurada e a origem das requisições deve corresponder ao endereço utilizado. Se o DHCP mudar o IP, atualize `--allowed-host` ao reiniciar. A autenticação Basic utiliza HTTP sem TLS: este modo se destina a redes confiáveis, sem encaminhamento público de porta. A porta precisa ser permitida pelo firewall da máquina e pelas regras da rede.

### Fotos e vídeos no computador de quem acessa a página

Em **Analisar imagens**, alterne entre **No servidor** e **Neste computador**. O segundo modo usa seleção de arquivos/pasta pelo navegador, prévia local, análise com OpenCV.js 4.13.0 em WebAssembly dentro de um Worker e decodificação de vídeo do próprio navegador. O vídeo é acessado por Blob URL; não é copiado inteiro para a memória do WASM e não é enviado. Formatos e codecs dependem do navegador. A aplicação não mede paralaxe 3D: usa ORB e homografia RANSAC, sem a matriz fundamental da análise Python. Os diagnósticos dos dois motores não são idênticos.

A triagem local amostra até 32 pares de fotos e 12 pares do vídeo escolhido. A extração considera trecho, intervalo mínimo, limite de candidatos, resolução, nitidez, movimento mínimo e intervalo de segurança. Ela preserva quadros sem correspondências suficientes para revisão. Até 512 MB de JPEGs extraídos ficam em memória por lote; atingir esse limite interrompe a tarefa preservando a seleção parcial. Cancelar uma tarefa local não cancela o processamento no servidor. As fotos extraídas não recebem GPS/SRT.

Fotos importadas não amostradas começam desmarcadas. A seleção pode ser revista antes de **Baixar diagnóstico JSON**, **Baixar fotos selecionadas em ZIP**, **Salvar em pasta deste computador** ou **Enviar fotos selecionadas**. ZIP usa fflate 0.8.2, com limite de 512 MB de fotos por arquivo para limitar uso de memória. Salvar diretamente em pasta usa File System Access API quando há contexto seguro (HTTPS ou localhost) e suporte do navegador; cada exportação cria uma subpasta nova. Em HTTP pela LAN, ou em navegadores sem esse recurso, use ZIP/JSON. Os arquivos locais e a seleção ficam apenas na memória da aba até serem salvos ou enviados; atualizá-la descarta esse estado.

O envio é explícito, foto por foto, sem incluir o vídeo. Cria um projeto novo na pasta global de projetos do servidor e grava `browser-import.json`. Não inicia reconstrução nem troca o projeto ativo. Requer no mínimo três fotos, até 50 MB por foto e até 10 GB/10.000 fotos por projeto. Os nomes e assinaturas de formato são verificados; a análise posterior ainda deve validar o conteúdo. Envios incompletos são preservados como projetos parciais, sem autorização para processar; não há retomada automática após reiniciar a interface. Para tentar novamente, use outro nome de projeto. A reconstrução ODM continua no servidor; seus produtos podem ser baixados para o computador do usuário.

As dependências ficam em `gui/web/vendor`, com licenças, origem e hashes, sem CDN em tempo de execução. A política de scripts da página permanece restrita. O Worker de análise permite os bindings dinâmicos necessários ao OpenCV.js, isolados da página.

Validação adicional:

```bash
node gui/test_local_worker.cjs
python -m gui.test_local_browser  # requer Firefox e ffmpeg
```

O teste real de navegador usa somente dados sintéticos e uma pasta temporária: verifica extração local, ausência de envio automático, envio explícito de JPEGs sem o vídeo, preservação de um processamento ativo e cancelamento.

### Identidade e compatibilidade

Paralaxe é a identidade da aplicação, do manifesto do navegador, dos ícones e das exportações locais (`paralaxe-fotos.zip`, `paralaxe-diagnostico.json`). O motor conserva seu nome OpenDroneMap, suas licenças, comandos, formatos e diretórios de saída. As credenciais novas usam o usuário `paralaxe`; `odm` continua aceito como alias para sessões existentes, com a mesma senha. O arquivo de senha é indicado explicitamente na inicialização; arquivos antigos continuam válidos. O comando `pixi run gui` permanece como alias de `pixi run paralaxe`. Os nomes técnicos `X-ODM-Token`, `odm_session`, opções da CLI e referências à imagem ODM permanecem compatíveis.


## Limites do diagnóstico e recuperação de falhas

Uma tentativa de alocação desproporcional no OpenMVS (por exemplo, centenas de
terabytes) pode acompanhar valores geométricos inválidos ou um defeito do motor.
O log isolado não identifica a causa e não prova falta de RAM física, corrupção
das fotos ou erro de extração. A triagem procura indícios antes da execução;
não calcula uma probabilidade de falha nem garante sucesso.

Antes de repetir uma reconstrução que falhou:

1. Preserve vídeo, fotos, metadados e logs. Confirme a existência de uma cópia
   antes de limpar intermediários ou refazer a extração.
2. Analise o trecho de vídeo ou a sequência de fotos na ferramenta independente.
   Revise pouca mudança, desfoque, saltos e ambiguidade geométrica. Uma homografia
   consistente pode ocorrer com giro da câmera, cena plana ou pouca paralaxe;
   não distingue essas causas sozinha.
3. Para vídeo, escolha um trecho útil e ajuste intervalo, limite de candidatos,
   nitidez e movimento. Extraia para outro projeto e revise a seleção obtida.
   Aumentar a quantidade de quadros semelhantes não garante melhor reconstrução.
4. Confira o diagnóstico do novo projeto antes de processar. Para retomar um
   projeto antigo, revise os backups e a limpeza reversível dos intermediários.
   Produtos finais e qualidade de reconstrução são escolhas separadas: pedir
   apenas ortofoto ainda exige reconstrução geométrica no motor.

## Histórico e manutenção

O [histórico da aplicação](CHANGELOG.md) resume as funcionalidades e a mudança de
identidade. A [documentação dos recursos visuais](web/assets/README.md) identifica
os arquivos editáveis. Para verificar uma instalação, execute a suíte Python,
o teste de Worker e a integração de navegador descritos acima. A integração usa
Firefox e ffmpeg e trabalha em pastas temporárias, sem depender de fotos privadas.
O manifesto fornece nome e ícones para o navegador; não há service worker nem
promessa de funcionamento completo offline.

### Georreferenciamento antes de processar

O processamento exige latitude, longitude e altitude GPS EXIF válidas em todas
as fotos e confirmação explícita no diagnóstico. A checagem lê os cabeçalhos de
todas as fotos; a análise de nitidez e movimento continua por amostragem.
GPS presente não certifica acurácia, sincronização, referência vertical ou escala.
Arquivos somente com XMP, posições externas ou GCP ainda exigem integração de
validação própria e são bloqueados por esta checagem conservadora.

A análise e a extração de vídeos continuam disponíveis, mas o processamento de
vídeos ou de quadros sem GPS fica bloqueado. A extração atual não transfere SRT
ou telemetria. Preserve o vídeo original, os SRTs e os registros de voo; arquivos
reexportados podem ter perdido esses dados. Não atribua a mesma coordenada a
quadros diferentes para contornar a checagem. Um GeoTIFF com EPSG e tamanho de
pixel pode conter pseudogeorreferenciamento; esses campos sozinhos não comprovam
posição ou resolução espacial reais.
