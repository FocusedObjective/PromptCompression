RESEARCH_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Prompt Compression Research</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #617083;
      --border: #d7dee8;
      --accent: #1769aa;
      --accent-dark: #0e4e84;
      --tag-bg: #eef2f8;
      --tag-text: #40506a;
      --shadow: 0 10px 30px rgba(24, 39, 75, 0.08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    main {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 44px;
    }

    header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0;
      font-size: 28px;
      line-height: 1.15;
      font-weight: 720;
    }

    h2 {
      margin: 0 0 12px;
      font-size: 18px;
      line-height: 1.25;
      font-weight: 760;
    }

    h3 {
      margin: 0;
      font-size: 15px;
      line-height: 1.3;
      font-weight: 720;
    }

    p {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }

    a {
      color: var(--accent);
      text-decoration: none;
    }

    a:hover {
      color: var(--accent-dark);
      text-decoration: underline;
    }

    .nav-links {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      margin-top: 8px;
    }

    .nav-link {
      color: var(--accent);
      font-size: 13px;
      font-weight: 680;
      text-decoration: none;
    }

    .summary {
      max-width: 780px;
    }

    .section {
      margin-top: 16px;
      padding: 16px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .resource {
      min-width: 0;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fbfcfe;
    }

    .resource-head {
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }

    .tag {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 8px;
      border-radius: 999px;
      background: var(--tag-bg);
      color: var(--tag-text);
      font-size: 12px;
      font-weight: 680;
      white-space: nowrap;
    }

    .resource-links {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 9px;
      font-size: 13px;
      font-weight: 680;
    }

    .notes {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
    }

    .notes li {
      margin: 5px 0;
    }

    @media (max-width: 860px) {
      header {
        align-items: stretch;
        flex-direction: column;
      }

      .grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Prompt Compression Research</h1>
        <p class="summary">Working bibliography for base models, compression papers, checkpoints, and implementation resources used or considered by this project.</p>
        <nav class="nav-links" aria-label="Primary navigation">
          <a class="nav-link" href="/">Compression UI</a>
          <a class="nav-link" href="/eval">Eval Suite</a>
          <a class="nav-link" href="/benchmark">Benchmark</a>
          <a class="nav-link" href="/changelog">Changelog</a>
          <a class="nav-link" href="/docs">API Docs</a>
        </nav>
      </div>
    </header>

    <section class="section">
      <h2>Current Decision</h2>
      <ul class="notes">
        <li>Keep LLMLingua-2 BERT-base as the runtime baseline until another candidate wins on this repo's evals.</li>
        <li>Benchmark LLMLingua-2 XLM-RoBERTa-large as the nearest quality-oriented alternative.</li>
        <li>Use PCToolkit as a benchmark/reference source, not as a production runtime dependency.</li>
        <li>Treat generative or abstractive compressors as offline teachers or challengers until they prove safe for IDs, JSON, schemas, code, and tenant-specific wording.</li>
        <li>Use tenant profiles first, then optional LoRA/adapters; avoid full per-tenant checkpoints by default.</li>
      </ul>
    </section>

    <section class="section">
      <h2>PCToolkit Assessment</h2>
      <ul class="notes">
        <li>Useful for benchmark shape: unified compressor interface, dataset/task taxonomy, and metric examples.</li>
        <li>Candidate ideas worth borrowing: LLMLingua-2 model switching, Selective Context as a simple extractive baseline, and secondary metrics such as BLEU, ROUGE, BERTScore, edit/fuzzy similarity, QA F1, retrieval scoring, and code similarity.</li>
        <li>Do not import it into the production API directly. The toolkit brings a broad dependency set, multiple model families, hard-coded API-key placeholders in the runner, high thread-count evaluation code, and task-specific prompts.</li>
        <li>Keep this repo's role-aware message handling, protected spans, JSON/TOON handling, and tenant audit logic as the production path.</li>
      </ul>
      <div class="resource-links">
        <a href="https://github.com/3DAgentWorld/Toolkit-for-Prompt-Compression" target="_blank" rel="noreferrer">PCToolkit GitHub</a>
        <a href="https://arxiv.org/abs/2403.17411" target="_blank" rel="noreferrer">PCToolkit Paper</a>
        <a href="https://arxiv.org/abs/2505.00019" target="_blank" rel="noreferrer">Empirical Study</a>
      </div>
    </section>

    <section class="section">
      <h2>Runtime Baselines And Checkpoints</h2>
      <div class="grid">
        <article class="resource">
          <div class="resource-head">
            <h3>LLMLingua-2 BERT-base multilingual MeetingBank</h3>
            <span class="tag">current baseline</span>
          </div>
          <p>Small extractive token-classification checkpoint used by the service today.</p>
          <div class="resource-links">
            <a href="https://huggingface.co/microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank" target="_blank" rel="noreferrer">Hugging Face</a>
            <a href="https://arxiv.org/abs/2403.12968" target="_blank" rel="noreferrer">Paper</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>LLMLingua-2 XLM-RoBERTa-large MeetingBank</h3>
            <span class="tag">benchmark</span>
          </div>
          <p>Larger LLMLingua-2 checkpoint to compare for quality versus latency and memory cost.</p>
          <div class="resource-links">
            <a href="https://huggingface.co/microsoft/llmlingua-2-xlm-roberta-large-meetingbank" target="_blank" rel="noreferrer">Hugging Face</a>
            <a href="https://arxiv.org/abs/2403.12968" target="_blank" rel="noreferrer">Paper</a>
          </div>
        </article>
      </div>
    </section>

    <section class="section">
      <h2>Core LLMLingua Lineage</h2>
      <div class="grid">
        <article class="resource">
          <div class="resource-head">
            <h3>LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models</h3>
            <span class="tag">EMNLP 2023</span>
          </div>
          <p>Original LLMLingua prompt compression work.</p>
          <div class="resource-links">
            <a href="https://aclanthology.org/2023.emnlp-main.825" target="_blank" rel="noreferrer">ACL Anthology</a>
            <a href="https://github.com/microsoft/LLMLingua" target="_blank" rel="noreferrer">GitHub</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression</h3>
            <span class="tag">ACL 2024</span>
          </div>
          <p>Long-context variant focused on improving long-context information processing and RAG-style prompts.</p>
          <div class="resource-links">
            <a href="https://aclanthology.org/2024.acl-long.91" target="_blank" rel="noreferrer">ACL Anthology</a>
            <a href="https://github.com/microsoft/LLMLingua" target="_blank" rel="noreferrer">GitHub</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression</h3>
            <span class="tag">ACL 2024 Findings</span>
          </div>
          <p>Extractive token-classification approach used as this project's baseline.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2403.12968" target="_blank" rel="noreferrer">arXiv</a>
            <a href="https://aclanthology.org/2024.findings-acl.57" target="_blank" rel="noreferrer">ACL Anthology</a>
            <a href="https://github.com/microsoft/LLMLingua" target="_blank" rel="noreferrer">GitHub</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>SecurityLingua: Efficient Defense of LLM Jailbreak Attacks via Security-Aware Prompt Compression</h3>
            <span class="tag">CoLM 2025</span>
          </div>
          <p>Security-oriented prompt compression from the LLMLingua research line.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2506.12707" target="_blank" rel="noreferrer">arXiv</a>
            <a href="https://aka.ms/SecurityLingua" target="_blank" rel="noreferrer">Project Link</a>
          </div>
        </article>
      </div>
    </section>

    <section class="section">
      <h2>Newer Compression Research To Track</h2>
      <div class="grid">
        <article class="resource">
          <div class="resource-head">
            <h3>An Empirical Study on Prompt Compression for Large Language Models</h3>
            <span class="tag">2025</span>
          </div>
          <p>Comparative study across several prompt-compression methods and datasets.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2505.00019" target="_blank" rel="noreferrer">arXiv</a>
            <a href="https://github.com/3DAgentWorld/Toolkit-for-Prompt-Compression" target="_blank" rel="noreferrer">GitHub</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Prompt Compression in the Wild</h3>
            <span class="tag">2026</span>
          </div>
          <p>Latency, rate-adherence, and quality study. Useful for deciding when compression actually pays for itself.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2604.02985" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Prompt Compression in Diffusion Large Language Models: Evaluating LLMLingua-2 on LLaDA</h3>
            <span class="tag">2026</span>
          </div>
          <p>Evaluates whether LLMLingua-2 compression transfers to diffusion LLMs.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2605.17932" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>DAC: A Dynamic Attention-aware Approach for Task-Agnostic Prompt Compression</h3>
            <span class="tag">2025</span>
          </div>
          <p>Attention-aware task-agnostic compression candidate. Relevant if an implementation becomes practical.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2507.11942" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>SCOPE: A Generative Approach for LLM Prompt Compression</h3>
            <span class="tag">2025</span>
          </div>
          <p>Generative chunking-and-summarization compressor. Good challenger or teacher, but risky as default runtime.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2508.15813" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Cmprsr: Abstractive Token-Level Question-Agnostic Prompt Compressor</h3>
            <span class="tag">2025</span>
          </div>
          <p>Small-LLM compressor trained for compression-rate adherence and downstream quality.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2511.12281" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>CompactPrompt: A Unified Pipeline for Prompt Data Compression in LLM Workflows</h3>
            <span class="tag">2025</span>
          </div>
          <p>Broader prompt and file-level compression pipeline. Useful for structured-data ideas.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2510.18043" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Prompt Compression with Context-Aware Sentence Encoding for Fast and Improved LLM Inference</h3>
            <span class="tag">2024</span>
          </div>
          <p>Sentence-level context-aware compressor with released code and dataset.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2409.01227" target="_blank" rel="noreferrer">arXiv</a>
            <a href="https://github.com/Workday/cpc" target="_blank" rel="noreferrer">GitHub</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>ACoRN: Noise-Robust Abstractive Compression in Retrieval-Augmented Language Models</h3>
            <span class="tag">2025</span>
          </div>
          <p>RAG-focused abstractive compressor with noise-robust training steps.</p>
          <div class="resource-links">
            <a href="https://arxiv.org/abs/2504.12673" target="_blank" rel="noreferrer">arXiv</a>
          </div>
        </article>
      </div>
    </section>

    <section class="section">
      <h2>Implementation Repositories And Package Resources</h2>
      <div class="grid">
        <article class="resource">
          <div class="resource-head">
            <h3>Microsoft LLMLingua</h3>
            <span class="tag">current compressor</span>
          </div>
          <p>Library used by the runtime compressor wrapper.</p>
          <div class="resource-links">
            <a href="https://github.com/microsoft/LLMLingua" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://pypi.org/project/llmlingua/" target="_blank" rel="noreferrer">PyPI</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Toolkit for Prompt Compression</h3>
            <span class="tag">benchmark reference</span>
          </div>
          <p>Research toolkit linked by the empirical prompt-compression study. Use for benchmark design and candidate wrappers, not as a production runtime dependency.</p>
          <div class="resource-links">
            <a href="https://github.com/3DAgentWorld/Toolkit-for-Prompt-Compression" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://arxiv.org/abs/2403.17411" target="_blank" rel="noreferrer">Paper</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Hugging Face Transformers</h3>
            <span class="tag">model loading</span>
          </div>
          <p>Underlying model/tokenizer stack for LLMLingua-2 checkpoints and possible direct adapter work.</p>
          <div class="resource-links">
            <a href="https://github.com/huggingface/transformers" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://huggingface.co/docs/transformers" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Hugging Face PEFT</h3>
            <span class="tag">LoRA candidate</span>
          </div>
          <p>Likely adapter/LoRA library if we move beyond rules-only tenant profiles.</p>
          <div class="resource-links">
            <a href="https://github.com/huggingface/peft" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://huggingface.co/docs/peft" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>PyTorch</h3>
            <span class="tag">ML runtime</span>
          </div>
          <p>Tensor and model runtime used through Transformers and LLMLingua.</p>
          <div class="resource-links">
            <a href="https://github.com/pytorch/pytorch" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://pytorch.org/docs/stable/index.html" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>FastAPI</h3>
            <span class="tag">HTTP API</span>
          </div>
          <p>API framework used by `app/main.py`.</p>
          <div class="resource-links">
            <a href="https://github.com/fastapi/fastapi" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://fastapi.tiangolo.com/" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Pydantic</h3>
            <span class="tag">schemas</span>
          </div>
          <p>Request and response schema validation.</p>
          <div class="resource-links">
            <a href="https://github.com/pydantic/pydantic" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://docs.pydantic.dev/" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Uvicorn</h3>
            <span class="tag">ASGI server</span>
          </div>
          <p>Local and container ASGI server for FastAPI.</p>
          <div class="resource-links">
            <a href="https://github.com/encode/uvicorn" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://www.uvicorn.org/" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Hugging Face Datasets</h3>
            <span class="tag">training data</span>
          </div>
          <p>Dataset utilities available for future training and evaluation workflows.</p>
          <div class="resource-links">
            <a href="https://github.com/huggingface/datasets" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://huggingface.co/docs/datasets" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>Requests</h3>
            <span class="tag">HTTP client</span>
          </div>
          <p>Simple HTTP client dependency used by scripts and smoke tests.</p>
          <div class="resource-links">
            <a href="https://github.com/psf/requests" target="_blank" rel="noreferrer">GitHub</a>
            <a href="https://requests.readthedocs.io/" target="_blank" rel="noreferrer">Docs</a>
          </div>
        </article>

        <article class="resource">
          <div class="resource-head">
            <h3>TOON Format</h3>
            <span class="tag">structured data</span>
          </div>
          <p>Compact structured-data format used by this repo's JSON-to-TOON compression path.</p>
          <div class="resource-links">
            <a href="https://toonformat.dev/" target="_blank" rel="noreferrer">Docs</a>
            <a href="https://pypi.org/project/toon-format/" target="_blank" rel="noreferrer">PyPI</a>
          </div>
        </article>
      </div>
    </section>
  </main>
</body>
</html>
"""
