# 深研资料: LangChain Retriever 的抽象层次与用法：它是开箱即用的组件还是底层封装接口？包括 Retriever 基类(BaseRetriever)的设计、常用实现(VectorStoreRetriever、MultiQueryRetriever、EnsembleRetriever、ContextualCompressionRetriever 等)的使用方式、与 LCEL 和 LangGraph 的集成模式，以及自定义 Retriever 的实现方法。

## BaseRetriever 基类的设计

- `BaseRetriever` 是一个抽象基类，继承自 `Runnable<string, DocumentInterface<Metadata>[]>`（JavaScript）或 `Runnable[str, List[Document]]`（Python），处理字符串查询并返回最相关的文档列表。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html) [[2]](https://python.langchain.com/api_reference/core/retrievers/langchain_core.retrievers.BaseRetriever.html)
- 提供通用属性和方法：`callbacks`、`tags`、`verbose` 等，供派生检索器使用。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 自定义检索器必须继承 `BaseRetriever` 并实现 `_getRelevantDocuments(query)`（JavaScript）或 `_get_relevant_documents(query)`（Python）方法。当前为占位方法，未实现时抛出错误，将在下一个大版本中变为抽象方法。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html) [[3]](https://github.com/langchain-ai/langchain/issues/13624)
- 支持 `batch` 方法（默认调用 `invoke` N 次），子类可重写优化。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 提供默认的流式实现，子类可通过重写 `_streamIterator` 或相应方法支持流式输出。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 可以将运行器转换为工具，通过 `toTool(name, description, schema)` 返回 `RunnableToolLike` 实例。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 支持 `stream` 和 `streamEvents` 方法，生成事件流，事件包括 `on_retriever_start` 和 `on_retriever_end`。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 在 Python 版本中，`BaseRetriever` 使用 `langchain_core` 模块定义，抽象方法 `_get_relevant_documents` 必须被实现，否则实例化时会触发 pydantic 验证错误。[[2]](https://python.langchain.com/api_reference/core/retrievers/langchain_core.retrievers.BaseRetriever.html) [[3]](https://github.com/langchain-ai/langchain/issues/13624)

## 常用 Retriever 实现

### VectorStoreRetriever

- `VectorStoreRetriever` 类扩展 `BaseRetriever`，用于从 `VectorStore` 基于向量相似性或最大边际相关性（MMR）检索文档。[[4]](https://reference.langchain.com/javascript/langchain-core/vectorstores/VectorStoreRetriever) [[6]](https://reference.langchain.com/python/langchain-core/vectorstores/base/VectorStoreRetriever)
- 构造参数：
  - `vectorstore`：必须实现 `VectorStoreInterface` 的实例。
  - `searchType`：可选 `"similarity"`（默认）或 `"mmr"`。
  - `searchKwargs`：可包含 `k`（默认 4）、`fetchK`、`lambda`（仅 MMR 时有效，0~1）。
  - `tags` 和 `metadata`：用于回调和子调用。[[4]](https://reference.langchain.com/javascript/langchain-core/vectorstores/VectorStoreRetriever)
- 主要方法：
  - `addDocuments`：提取文本、生成嵌入并添加到向量存储。
  - `invoke`：调用 `transformDocuments` 处理输入。
  - `batch`：默认 N 次调用 `invoke`，子类可重写优化。
  - `stream`：分块输出。
  - `streamEvents`：生成实时事件流，事件格式包含 `event`、`name`、`run_id`、`tags`、`metadata`、`data`。[[4]](https://reference.langchain.com/javascript/langchain-core/vectorstores/VectorStoreRetriever)
- 可以通过 `get_input_schema()` 和 `get_output_schema()` 获取输入/输出 Pydantic 模型，用于验证；通过 `configurable_fields()` 列出可配置字段；通过 `get_name()` 获取 Runnable 名称。[[6]](https://reference.langchain.com/python/langchain-core/vectorstores/base/VectorStoreRetriever)

### ContextualCompressionRetriever

- `ContextualCompressionRetriever` 继承自 `BaseRetriever`，是一个包装基础检索器并压缩结果的检索器。[[8]](https://reference.langchain.com/python/langchain-classic/retrievers/contextual_compression/ContextualCompressionRetriever)
- 接受一个基检索器（`BaseRetriever`）用于获取相关文档，以及一个压缩器（`Compressor`）用于压缩检索到的文档。[[8]](https://reference.langchain.com/python/langchain-classic/retrievers/contextual_compression/ContextualCompressionRetriever)

## 与 LCEL 的集成模式

- `create_history_aware_retriever` 函数来自 `langchain.chains`，接收 `llm` 和 `retriever` 参数，用于构建历史感知检索器。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 使用 LCEL 实现等价行为，无需 `create_history_aware_retriever`，通过 `ChatPromptTemplate`、`MessagesPlaceholder`、`RunnablePassthrough` 和管道操作符 `|` 构建查询重写链和检索链。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 查询重写提示模板示例：使用 `ChatPromptTemplate.from_messages`，包含 `("system", "...")`、`MessagesPlaceholder("chat_history")` 和 `("human", "{question}")`。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 重写链定义为 `rewrite_chain = rewrite_prompt | llm_rewriter | (lambda m: m.content)`，从 LLM 输出中提取文本。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 检索链定义为 `retrieval_chain = rewrite_chain | retriever`，将重写后的查询直接传给检索器。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 完整 RAG 链通过字典绑定 `"context"`、`"question"`、`"chat_history"` 后接 `answer_prompt | llm_answer`，其中 `"context"` 映射到 `retrieval_chain`，`"question"` 和 `"chat_history"` 映射到 `RunnablePassthrough()`。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- LCEL 管道调用时传入字典 `{"chat_history": chat_history_messages, "question": "What are its symptoms?"}`，输出使用 `.content` 属性。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)
- 推荐检索参数 `search_kwargs={"k": 4}`，`k` 值在 4–8 之间适用于高质量嵌入（如 BGE-large, ada-002）。[[11]](https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec)

## 自定义 Retriever 的实现方法

- 自定义检索器必须继承 `BaseRetriever` 并实现以下抽象方法：
  - Python：`_get_relevant_documents(query: str, *, run_manager: CallbackManagerForRetrieverRun) -> List[Document]`。[[2]](https://python.langchain.com/api_reference/core/retrievers/langchain_core.retrievers.BaseRetriever.html) [[3]](https://github.com/langchain-ai/langchain/issues/13624)
  - JavaScript：`_getRelevantDocuments(query: string, options: { callbacks?: Callbacks })`。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 子类可以通过重写 `_streamIterator` 或相应方法支持流式输出。[[1]](https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html)
- 实现时需注意避免名称修饰问题（如 Python 中的 name mangling），确保抽象方法签名与基类完全一致，否则会触发 pydantic 验证错误。[[3]](https://github.com/langchain-ai/langchain/issues/13624)

## 来源

- https://reference.langchain.com/javascript/classes/_langchain_core.retrievers.BaseRetriever.html
- https://python.langchain.com/api_reference/core/retrievers/langchain_core.retrievers.BaseRetriever.html
- https://github.com/langchain-ai/langchain/issues/13624
- https://reference.langchain.com/javascript/langchain-core/vectorstores/VectorStoreRetriever
- https://reference.langchain.com/python/langchain-core/vectorstores/base/VectorStoreRetriever
- https://reference.langchain.com/python/langchain-classic/retrievers/contextual_compression/ContextualCompressionRetriever
- https://www.linkedin.com/pulse/history-aware-retriever-langchain-deep-dive-guide-ganesh-jagadeesan-nahec