"""轻量 RAG：切块 + embedding 检索。
索引在 parse_report（全文唯一完整存在处）构建，不进 state（防 checkpointer 膨胀）。"""
import os
import pickle
from pathlib import Path
from collections import Counter
import re
from openai import OpenAI
import numpy as np
from dotenv import load_dotenv


load_dotenv()

INDEX_DIR = Path(__file__).resolve().parent.parent / "output" / "rag_index"
EMBED_MODEL = os.getenv("EMBED_MODEL", "embedding-3")

_embed_client = None
if os.getenv("EMBED_API_KEY"):
    _embed_client = OpenAI(api_key=os.getenv("EMBED_API_KEY"),
                           base_url=os.getenv("EMBED_BASE_URL"))


def _embed(texts: list[str]) -> list[list[float]]:
    """分批嵌入（embedding 接口每批上限 64 条）。"""
    if _embed_client is None:
        raise RuntimeError("未配置 EMBED_API_KEY")
    out = []
    for i in range(0, len(texts), 64):
        resp = _embed_client.embeddings.create(
            model=EMBED_MODEL, input=[t[:8000] for t in texts[i:i+64]])
        out.extend(d.embedding for d in resp.data)
    return out


def _bigrams(s: str) -> Counter:
    s = re.sub(r"\s+", "", s)
    return Counter(s[i:i+2] for i in range(len(s) - 1))


class VectorStore:
    """stem → {chunks, vecs, grams}。进程内 + 落盘，重启不重嵌入。"""

    def __init__(self):
        self._data: dict[str, dict] = {}
        INDEX_DIR.mkdir(parents=True, exist_ok=True)

    def build(self, stem: str, full_text: str):
        chunks = self._chunk(full_text)
        if not chunks:
            print("⚠️ RAG：切块为空，跳过")
            return
        entry = {"chunks": chunks, "grams": [_bigrams(c) for c in chunks]}
        try:
            vecs = np.array(_embed(chunks), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
            entry["vecs"] = vecs
            (INDEX_DIR / f"{stem}.pkl").write_bytes(pickle.dumps(entry))
            print(f"📚 索引就绪: {stem}（{len(chunks)} 块，向量模式）")
        except Exception as e:
            entry["vecs"] = None
            print(f"⚠️ 嵌入失败退回关键词模式: {e}")
            print(f"📚 索引就绪: {stem}（{len(chunks)} 块，关键词模式）")
        self._data[stem] = entry

    def load(self, stem: str) -> bool:
        if stem in self._data:
            return True
        p = INDEX_DIR / f"{stem}.pkl"
        if p.exists():
            self._data[stem] = pickle.loads(p.read_bytes())
            print(f"📚 索引载入: {stem}")
            return True
        return False

    def retrieve(self, stem: str, query: str, k: int = 8) -> list[str]:
        if not self.load(stem):
            return []
        e = self._data[stem]
        if e.get("vecs") is not None:
            q = np.array(_embed([query])[0], dtype=np.float32)
            q /= np.linalg.norm(q) + 1e-9
            top = np.argsort(-(e["vecs"] @ q))[:k]
        else:
            qg = _bigrams(query)
            scores = [sum((qg & g).values()) / (sum(g.values()) or 1)
                      for g in e["grams"]]
            top = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [e["chunks"][i] for i in top]

    @staticmethod
    def _chunk(text: str, size: int = 800, overlap: int = 120) -> list[str]:
        """滑窗切块：按段落累积到 ~800 字成块，块间保留 120 字重叠。"""
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        chunks, buf, n = [], [], 0
        for p in paras:
            buf.append(p); n += len(p)
            if n >= size:
                chunks.append("\n".join(buf))
                tail, t = [], 0
                for x in reversed(buf):
                    if t >= overlap: break
                    tail.append(x); t += len(x)
                buf, n = tail[::-1], t
        if buf and (not chunks or len("\n".join(buf)) > overlap):
            chunks.append("\n".join(buf))
        return chunks


STORE = VectorStore()
