# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# TODO: Remove below import when minimum supported Python version is 3.10
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Iterable, List, Optional, Tuple, Type, Union

import nest_asyncio  # type: ignore
import numpy as np
from langchain_community.vectorstores.utils import maximal_marginal_relevance
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from sqlalchemy import text

from .mysql_engine import MySQLEngine

class CloudSQLVectorStore(VectorStore):
    """Google Cloud SQL for PostgreSQL Vector Store class"""

    def __init__(
        self,
        engine: MySQLEngine,
        embedding_service: Embeddings,
        table_name: str,
        content_column: str = "content",
        embedding_column: str = "embedding",
        metadata_columns: List[str] = [],
        ignore_metadata_columns: Optional[List[str]] = None,
        id_column: str = "langchain_id",
        metadata_json_column: str = "langchain_metadata",
        # index_query_options: Optional[
        #     HNSWIndex.QueryOptions | IVFFlatIndex.QueryOptions
        # ] = None,
        # distance_strategy: DistanceStrategy = DEFAULT_DISTANCE_STRATEGY,
        overwrite_existing: bool = False,
        # k: Optional[int] = None,
        # score_threshold: Optional[float] = None,
        # fetch_k: Optional[int] = None,
        # lambda_mult: Optional[float] = None,
    ):
        """_summary_
        Args:
            engine (PostgreSQLEngine): _description_
            embedding_service (Embeddings): _description_
            table_name (str): _description_
            content_column (str): _description_
            embedding_column (str): _description_
            metadata_columns (List[str]): _description_
            ignore_metadata_columns (List[str]): _description_
            id_column (str): _description_
            metadata_json_column (str): _description_
            index_query_options (_type_): _description_
            distance_strategy (DistanceStrategy, optional): _description_. Defaults to DEFAULT_DISTANCE_STRATEGY.
        """
        self.engine = engine
        self.embedding_service = embedding_service
        self.table_name = table_name
        self.content_column = content_column
        self.embedding_column = embedding_column
        self.metadata_columns = metadata_columns
        self.ignore_metadata_columns = ignore_metadata_columns
        self.id_column = id_column
        self.metadata_json_column = metadata_json_column
        # self.index_query_options = index_query_options
        # self.distance_strategy = distance_strategy
        self.overwrite_existing = overwrite_existing
        # self.store_metadata = False  # Set true later
        # self.k = k
        # self.score_threshold = score_threshold
        # self.fetch_k = fetch_k
        # self.lambda_mult = lambda_mult
        if metadata_columns and ignore_metadata_columns:
            raise ValueError(
                "Can not use both metadata_columns and ignore_metadata_columns."
            )
        self.__post_init__()




    def __post_init__(self) -> None:
        # Truncate the table if overwrite_existing
        if self.overwrite_existing:
            with self.engine.connect() as conn:
                conn.execute(text(f"TRUNCATE TABLE {self.table_name}"))
                conn.commit()

        stmt = text(
            f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{self.table_name}'"
        )

        # async with self.engine.connect() as conn:
        # results = await self.engine._aexecute_fetch(stmt)
        with self.engine.connect() as conn:
            results = conn.execute(stmt)

        # Get field type information
        columns = {}
        for field in results.fetchall():
            columns[field[0]] = field[1]

        if self.id_column not in columns:
            raise ValueError(f"Id column, {self.id_column}, does not exist.")
        if self.content_column not in columns:
            raise ValueError(f"Content column, {self.content_column}, does not exist.")
        if self.embedding_column not in columns:
            raise ValueError(
                f"Embedding column, {self.embedding_column}, does not exist."
            )
        if columns[self.embedding_column] != "USER-DEFINED":
            raise ValueError(
                f"Embedding column, {self.embedding_column}, is not type Vector."
            )
        for column in self.metadata_columns:
            if column not in columns:
                raise ValueError(f"Metadata column, {column}, does not exist.")
        # if column_types[content_column] is not "String":
        #     raise ValueError(f"Content column, {content_column}, does not exist.")
        if self.metadata_json_column in columns:
            self.store_metadata = True

        all_columns = columns  # .keys()
        if self.ignore_metadata_columns:
            for column in self.ignore_metadata_columns:
                del all_columns[column]

            del all_columns[self.id_column]
            del all_columns[self.content_column]
            del all_columns[self.embedding_column]
            self.metadata_columns = [k for k, v in all_columns.keys()]

    @property
    def embeddings(self) -> Embeddings:
        return self.embedding_service
    
    def add_embeddings(
        self,
        texts: Iterable[str],
        embeddings: List[List[float]],
        metadatas: Optional[List[dict]] = None,
        ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[str]:
        if not ids:
            ids = [str(uuid.uuid4()) for _ in texts]
        if not metadatas:
            metadatas = [{} for _ in texts]
        for id, content, embedding, metadata in zip(ids, texts, embeddings, metadatas):
            metadata_col_names = (
                ", " + ", ".join(self.metadata_columns)
                if len(self.metadata_columns) > 0
                else ""
            )
            insert_stmt = f"INSERT INTO {self.table_name}({self.id_column}, {self.content_column}, {self.embedding_column}{metadata_col_names}"
            values_stmt = f" VALUES ('{id}','{content}','{embedding}'"
            extra = metadata
            for metadata_column in self.metadata_columns:
                values_stmt += f",'{metadata[metadata_column]}'"
                del extra[metadata_column]

            insert_stmt += (
                f", {self.metadata_json_column})" if self.store_metadata else ")"
            )
            values_stmt += f",'{extra}')" if self.store_metadata else ")"
            query = insert_stmt + values_stmt
            # await self.engine._aexecute_update(query)
            with self.engine.connect() as conn:
                conn.execute(text(query))
                conn.commit()
        return ids

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: List[dict] = None,
        ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[str]:
        # if ids is None:
        #     ids = [str(uuid.uuid1()) for _ in texts]
        embeddings = self.embedding_service.embed_documents(list(texts))
        ids = self.add_embeddings(
            texts, embeddings, metadatas=metadatas, ids=ids, **kwargs
        )
        return ids