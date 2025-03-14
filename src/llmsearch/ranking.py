import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from typing import List
from llmsearch.config import SemanticSearchConfig

# from llmsearch.utils import LLMBundle
from typing import Tuple
import statistics

from loguru import logger
from sentence_transformers.cross_encoder import CrossEncoder

from llmsearch.config import Document




class MarcoReranker:
    def __init__(
        self, cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    ) -> None:
        logger.info("Initializing Reranker...")
        self._crdss_encoder_model_name = cross_encoder_model
        self.model = CrossEncoder(cross_encoder_model)
        logger.info("Initialized MS-MARCO Reranker")

    def get_scores(self, query: str, docs: List[Document]) -> List[float]:
        logger.info("Reranking documents ... ")

        features = [[query, doc.page_content] for doc in docs]
        scores = self.model.predict(features).tolist()
        return scores
    
class BGEReranker:
    def __init__(self) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained('BAAI/bge-reranker-base')
        self.model = AutoModelForSequenceClassification.from_pretrained('BAAI/bge-reranker-base')
        self.model.eval()
        logger.info("Initialized BGE-base Reranker")

    def get_scores(self, query: str, docs: List[Document]) -> List[float]:
        logger.info("Reranking documents ... ")
        features = [[query, doc.page_content] for doc in docs]
        with torch.no_grad():
            inputs = self.tokenizer(features, padding=True, truncation=True, return_tensors='pt', max_length=512)
            scores = self.model(**inputs, return_dict=True).logits.view(-1, ).float().tolist()
        return scores
        

def rerank(rerank_model, query: str, docs: List[Document]) -> Tuple[float, List[Document]]:
    logger.info("Reranking documents ... ")
    scores = rerank_model.get_scores(query, docs)
    print(scores)
    for score, d in zip(scores, docs):
        d.metadata["score"] = score

    sorted_scores = sorted(scores, reverse=True)

    logger.info(sorted_scores)
    median_ = statistics.mean(sorted_scores[:5])
    return median_, [
        doc for doc in sorted(docs, key=lambda d: d.metadata["score"], reverse=True)
    ]
        
    
def get_relevant_documents(original_query: str, queries: List[str], llm_bundle, config: SemanticSearchConfig, label: str, 
) -> Tuple[List[str], float]:
    most_relevant_docs = []
    docs = []


    # original_query = queries[0]
    sparse_retriever = llm_bundle.sparse_search

    # Get max_k  documents using sparse search

    current_reranker_score, reranker_score = -1e5, -1e5

    # Iterate over all available chunk sizes
    for chunk_size in llm_bundle.chunk_sizes:
        all_relevant_docs = []
        all_relevant_doc_ids = set()
        for query in queries:
            logger.debug("Evaluating query: {}", query)
            if config.query_prefix:
                logger.info(f"Adding query prefix for retrieval: {config.query_prefix}")
                query = config.query_prefix + query
            sparse_search_docs_ids, sparse_scores = sparse_retriever.query(
                search=query, n=config.max_k, label = label, chunk_size = chunk_size
            )

            logger.info(f"Stage 1: Got {len(sparse_search_docs_ids)} documents.")

            # Set a filter for current chunk size or skip filter if only one chunk size is present (considerably faster)
            filter = {"chunk_size": chunk_size} if len(llm_bundle.chunk_sizes) > 1 else dict()
            
            # Add label to filter, if present
            if label:
                filter.update({"label": label})
            
            if not filter: # if filter is empty (doesn't contain chunk_size or label), set it to None to speed up.
                filter = None
                
            logger.info(f"Dense embeddings filter: {filter}")

            res = llm_bundle.store.similarity_search_with_relevance_scores(
                query, k=config.max_k, filter=filter
            )
            dense_search_doc_ids = [r[0].metadata["document_id"] for r in res]

            # Create union of documents fetched using sprase and dense embeddings
            all_doc_ids = (set(sparse_search_docs_ids).union(set(dense_search_doc_ids))).difference(all_relevant_doc_ids)
            logger.debug("NUMBER OF NEW DOCS to RETRIEVE: {}", len(all_doc_ids))
            if all_doc_ids:
                relevant_docs = llm_bundle.store.get_documents_by_id(
                    document_ids=list(all_doc_ids)
                )
                all_relevant_docs+=relevant_docs
                all_relevant_doc_ids = all_relevant_doc_ids.union(all_doc_ids)

        # Choose chunk size that is best suitable to answer the questoin
        # Re-rank embeddings
        if llm_bundle.reranker is not None:
            #reranker_score, relevant_docs = llm_bundle.reranker.rerank(
                #original_query, all_relevant_docs
            #)
            reranker_score, relevant_docs = rerank(rerank_model = llm_bundle.reranker,
                query = original_query, docs = all_relevant_docs
            )
            if reranker_score > current_reranker_score:
                logger.info("New most relevant query: {}", original_query)
                docs = relevant_docs
                current_reranker_score = reranker_score

        #logger.info(
            #f"Number of documents after stage 1 (sparse): {len(sparse_search_docs_ids)}"
        #)
        logger.info(
            f"Number of documents after stage 2 (dense + sparse): {len(all_relevant_docs)}"
        )
        logger.info(
            f"Re-ranker avg. scores for top 5 resuls, chunk size {chunk_size}: {reranker_score:.2f}"
        )

    len_ = 0

    for doc in docs:
        doc_length = len(doc.page_content)
        if len_ + doc_length < config.max_char_size:
            most_relevant_docs.append(doc)
            len_ += doc_length

    return most_relevant_docs, current_reranker_score