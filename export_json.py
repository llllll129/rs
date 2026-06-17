import json, base64, numpy as np
from chromadb import PersistentClient
client = PersistentClient(path='rs_knowledge_db')
col = client.get_collection('rs_course')
data = col.get(include=['embeddings','metadatas','documents'])
emb_list = []
for emb in data['embeddings']:
    emb_list.append(base64.b64encode(np.array(emb, dtype=np.float32).tobytes()).decode())
out = {'ids': data['ids'], 'embeddings': emb_list, 'metadatas': data['metadatas'], 'documents': data['documents']}
with open('rs_knowledge_db.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False)
print(f'导出完成，共 {len(out["ids"])} 条')