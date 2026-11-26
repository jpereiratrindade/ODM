# Interface experimental ODM

Interface local em português, inspirada na estrutura e nas cores do
[SITUS](https://github.com/jpereiratrindade/SITUS/tree/main/web): menu lateral,
painéis claros, azul e teal, SVG local e ausência de CDN ou telemetria.
Protótipo independente da CLI existente, sem dependências Python adicionais.

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
levantamento exige o ambiente ODM e os binários nativos compilados.

As três seções têm telas próprias: **Meu levantamento**, **Planejar um voo** e
**Meus produtos**. Escolha um perfil visual (rápido, equilibrado ou detalhado),
marque os produtos desejados e clique **Criar meus produtos**. O acompanhamento
mostra grupos de etapas identificadas na saída real do ODM, sem percentual estimado.
O planejamento inclui um esquema do voo e os resultados são apresentados em
cartões, com indicação de quais arquivos existem no projeto. As ilustrações dos
cartões representam tipos de produto, não são prévias dos arquivos.

**Detalhes técnicos e diagnóstico**, recolhido por padrão, contém logs e o
botão **Mostrar comando ODM**, com os argumentos exatos como strings entre aspas.
O processamento chama `run.py` com o Python do servidor, sem shell.
Há uma execução por servidor, logs reais com atualização a cada 1,5 segundo e
interrupção do grupo de processos em Linux/macOS. Em Windows, a interrupção
atinge o processo Python principal; processos nativos filhos podem continuar.

O ODM grava resultados no projeto e pode retomar etapas existentes segundo suas
regras habituais. O inventário mostra produtos conhecidos; não oferece upload,
download, visualizador geográfico ou gerenciamento persistente de tarefas.
Logs e estado da interface ficam em memória, com limite de 2.000 linhas. Fechar
a aba não interrompe a execução; encerrar o servidor com Ctrl+C solicita parada.

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
python -m unittest gui.test_server -v
node --check gui/web/app.js
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
