"""
Regressão do bug de cache em image_providers.find_images.

Bug original: a verificação de cache hit usava índice FIXO 0 enquanto a gravação
usava um `run_index` GLOBAL. Com count>1, a 2ª run (cache quente) só recuperava o
slot 0 e dava `continue`, devolvendo MENOS imagens que `count` em silêncio (atinge
One Piece [count=pool] e futebol [count=n]).

Fix: índice POR-PROVIDER (`provider_index`) + drain dos slots 0..N do cache, com o
drain alimentando o dedupe (hash + source_url) pra não re-servir imagem.

Roda sem rede (provider fake com bytes embutidos):
    .venv\\Scripts\\python.exe tests\\test_cache_index.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import image_providers as ip  # noqa: E402


def _make_fake_provider(n_distinct):
    """Provider fake: devolve n_distinct imagens DISTINTAS (bytes únicos) e conta chamadas."""
    state = {"calls": 0}

    def _fake(query, count, *args):
        state["calls"] += 1
        out = []
        for i in range(n_distinct):
            data = b"\xff\xd8\xff" + bytes([i]) * 5000  # >2048 bytes, único por índice
            out.append(ip.ImageResult(
                url=f"https://fake.example/{query}/{i}.jpg",
                license="cc0",  # burn-safe → passa o gate da lane burn
                attribution="",
                source_provider="fakeprov",
                source_url=f"https://fake.example/page/{i}",
                bytes_data=data,
            ))
        return out

    return _fake, state


def _two_runs(tmp, count):
    """Roda find_images 2x no mesmo cwd (cache frio → quente). Retorna (r1, r2, calls_run2)."""
    os.makedirs(tmp, exist_ok=True)
    os.chdir(tmp)
    fake, state = _make_fake_provider(3)
    ip._PROVIDER_REGISTRY["fakeprov"] = fake
    r1 = ip.find_images("courtroom", "true-crimes", "burn", count=count, providers=["fakeprov"])
    calls_after_1 = state["calls"]
    r2 = ip.find_images("courtroom", "true-crimes", "burn", count=count, providers=["fakeprov"])
    calls_run2 = state["calls"] - calls_after_1
    return r1, r2, calls_run2


def main():
    base = tempfile.mkdtemp(prefix="cd_cache_test_")

    # --- count=3: o caso que o bug quebrava ---
    r1, r2, calls_run2 = _two_runs(os.path.join(base, "c3"), 3)
    assert len(r1) == 3, f"[count=3 run1] esperava 3 imagens, veio {len(r1)}"
    assert len(r2) == 3, (
        f"[count=3 run2/cache quente] esperava 3, veio {len(r2)} "
        f"<-- ESTE era o bug (vinha 1)"
    )
    assert calls_run2 == 0, (
        f"[count=3 run2] provider NAO devia ser chamado (cache cheio), foi {calls_run2}x"
    )
    assert len({p.name for p in r2}) == 3, "[count=3] os 3 slots devem ser arquivos distintos"

    # --- count=1: regressão (tem que continuar idêntico ao comportamento antigo) ---
    r1b, r2b, calls_run2b = _two_runs(os.path.join(base, "c1"), 1)
    assert len(r1b) == 1 and len(r2b) == 1, (
        f"[count=1] esperava 1/1, veio {len(r1b)}/{len(r2b)}"
    )
    assert calls_run2b == 0, f"[count=1 run2] provider nao devia ser chamado, foi {calls_run2b}x"

    print("OK: cache por-provider drena TODOS os slots; count>1 respeitado na run cacheada; "
          "count=1 intacto; provider nao re-chamado com cache quente.")


if __name__ == "__main__":
    main()
