"""
Ngày 14 — Quy trình đánh giá và đo chuẩn AI
AICB-P1: Chương trình Năng lực Thực hành AI, Giai đoạn 1

Các khái niệm chính từ bài giảng:
    - Đánh giá = Phương pháp khoa học cho AI (Giả thuyết → Thí nghiệm → Đo lường → Kết luận → Lặp lại)
    - 4 nhóm chỉ số: Hoàn thành tác vụ, Chất lượng câu trả lời, Chỉ số riêng cho RAG, Kinh doanh
    - Chỉ số quy trình RAG: Context Recall → Context Precision → Faithfulness → Answer Relevancy
    - LLM làm giám khảo: chấm rubric từ 1-5, phát hiện thiên kiến (vị trí, dài dòng, ưu tiên bản thân)
    - Bộ dữ liệu chuẩn: lấy mẫu phân tầng (5 Dễ + 7 Trung bình + 5 Khó + 3 Đối kháng)
    - Phân loại lỗi: hallucination, irrelevant, incomplete, off_topic, refusal
    - Phương pháp 5 Whys để phân tích nguyên nhân gốc rễ
    - Tích hợp CI/CD: dùng đánh giá làm cổng chất lượng (điểm < ngưỡng = chặn triển khai)
    - Vòng lặp cải tiến liên tục: Đánh giá → Phân tích → Cải tiến → Bổ sung → Lặp lại

Hướng dẫn:
    1. Hoàn thiện mọi phần bắt buộc được đánh dấu TODO.
    2. KHÔNG thay đổi chữ ký của lớp/hàm. Tham số tùy chọn ``contexts``
       trong ``run_full_eval`` là một phần của giao diện bắt buộc.
    3. Khi hoàn tất, sao chép tệp này sang solution/solution.py.
    4. Chạy: pytest tests/ -v

Hàm hỗ trợ xếp hạng lại là bài tập thưởng tùy chọn và có thể chưa cần triển khai.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Tác vụ 1 — Mô hình dữ liệu (Bộ dữ liệu chuẩn + Kết quả đánh giá)
# ---------------------------------------------------------------------------


@dataclass
class QAPair:  # tạo class QAPair để lưu trữ cặp câu hỏi-câu trả lời
    """
    Một cặp câu hỏi-câu trả lời dùng để đánh giá (thuộc Bộ dữ liệu chuẩn).

    Theo bài giảng, Bộ dữ liệu chuẩn cần có:
        - question: câu hỏi của người dùng
        - ground_truth (expected_answer): câu trả lời kỳ vọng do chuyên gia viết
        - context: tài liệu nguồn cần truy xuất
        - metadata: độ khó (easy/medium/hard), danh mục, tài liệu nguồn

    Các trường:
        question:           Câu hỏi cần trả lời.
        expected_answer:    Câu trả lời tham chiếu/đáp án chuẩn (do chuyên gia viết).
        context:            Ngữ cảnh nguồn (có thể là chuỗi rỗng nếu không áp dụng).
        metadata:           Từ điển siêu dữ liệu tùy chọn (độ khó, danh mục, v.v.).
        retrieved_contexts: Danh sách các đoạn được truy xuất (THỨ TỰ = thứ hạng từ bộ truy xuất).
                            Dùng cho các chỉ số phía truy xuất (Tác vụ 2b).
    """

    # Gợi ý:
    question: str = ""
    expected_answer: str = ""
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieved_contexts: list[str] = field(default_factory=list)
    pass


@dataclass
class EvalResult:  # tạo class EvalResult để lưu trữ kết quả đánh giá
    """
    Kết quả đánh giá cho một cặp hỏi-đáp.

    Theo bài giảng - quy trình chỉ số RAG:
        Question → Retriever → Context → Generator → Answer
        Mỗi bước có một chỉ số: Context Recall, Context Precision, Faithfulness, Answer Relevancy

    Theo bài giảng - cách diễn giải điểm:
        0.8-1.0: Tốt (Theo dõi, duy trì)
        0.6-0.8: Cần cải thiện (Phân tích lỗi, lặp lại)
        < 0.6: Có vấn đề đáng kể (Cần điều tra sâu)

    Các trường:
        qa_pair:        Đối tượng QAPair ban đầu.
        actual_answer:  Câu trả lời thực tế do tác tử trả về.
        faithfulness:   Số thực 0-1, mức độ câu trả lời bám sát ngữ cảnh.
        relevance:      Số thực 0-1, mức độ câu trả lời liên quan đến câu hỏi.
        completeness:   Số thực 0-1, mức độ đầy đủ so với câu trả lời kỳ vọng.
        passed:         True nếu cả ba điểm đều >= 0.5.
        failure_type:   None nếu đạt, nếu không thì là một trong các giá trị:
                        "hallucination", "irrelevant", "incomplete", "off_topic".
        context_precision: Số thực 0-1 hoặc None — chất lượng thứ hạng truy xuất.
        context_recall:    Số thực 0-1 hoặc None — mức độ ngữ cảnh bao phủ đáp án kỳ vọng.
                        (Cả hai giữ nguyên là None nếu không cung cấp các đoạn truy xuất;
                         chúng KHÔNG thuộc overall_score().)
    """

    qa_pair: QAPair
    actual_answer: str = ""
    faithfulness: float | None = None
    relevance: float | None = None
    completeness: float | None = None
    passed: bool = False
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    def overall_score(self) -> float:
        """Tính trung bình của faithfulness, relevance và completeness.

        Trả về:
            (faithfulness + relevance + completeness) / 3.0



        """
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


# ---------------------------------------------------------------------------
# Tác vụ 2 — Bộ đánh giá RAGAS (Heuristic chồng lấp từ đơn giản hóa)
# ---------------------------------------------------------------------------
# Trong môi trường production, thay bằng framework RAGAS thực tế:
#   from ragas import evaluate
#   from ragas.metrics import Faithfulness, AnswerRelevancy, ContextRecall, ContextPrecision
#
# Hoặc DeepEval:
#   from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
#   assert_test(test_case, [faithfulness, hallucination])
#
# Hoặc TruLens:
#   from trulens.core import Feedback
#   f_groundedness = Feedback(provider.groundedness_measure_with_cot_reasons)
# ---------------------------------------------------------------------------

# Bỏ qua các từ dừng tiếng Anh phổ biến để độ chồng lấp phản ánh từ mang *nội dung*,
# không phải từ đệm (nếu không, "is"/"a"/"the" sẽ làm tăng mọi điểm số).
STOPWORDS: set[str] = {  # tạo set các từ dừng tiếng Anh phổ biến
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "as",
    "by",
    "and",
    "or",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "from",
    "into",
    "than",
}


def _tokenize(text: str) -> set[str]:
    """Tách từ sau khi chuyển thành chữ thường, bỏ qua dấu câu và từ dừng."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


# RAGAS: Retrieval-Augmented Generation Assessment. framework đánh giá chất lượng câu trả lời của mô hình RAG (Retrieval-Augmented Generation) dựa trên các tiêu chí như Faithfulness, Relevance, Completeness, Context Recall và Context Precision.
class RAGASEvaluator:
    """
    Đánh giá đầu ra của quy trình RAG bằng các heuristic lấy cảm hứng từ RAGAS.

    Để đơn giản, mọi chỉ số đều dùng độ chồng lấp từ thay vì gọi LLM.
    Trong môi trường production, hãy thay bằng cách đánh giá thực tế dựa trên LLM.
    """

    def evaluate_faithfulness(
        self, answer: str, context: str
    ) -> float:  # hàm đánh giá mức độ bám sát ngữ cảnh của câu trả lời
        """
        Đo mức độ câu trả lời bám sát ngữ cảnh.

        Heuristic:
            answer_tokens = _tokenize(answer)
            context_tokens = _tokenize(context)
            faithfulness = |answer_tokens ∩ context_tokens| / |answer_tokens|
            Giới hạn trong [0.0, 1.0]. Trả về 1.0 nếu câu trả lời rỗng.

        Trả về:
            Số thực trong [0.0, 1.0] — 1.0 = hoàn toàn bám sát ngữ cảnh.
        """
        answer_tokens = _tokenize(answer)
        context_tokens = _tokenize(context)
        if not answer_tokens:
            return 1.0
        intersection = answer_tokens.intersection(context_tokens)
        faithfulness = len(intersection) / len(answer_tokens)
        return max(0.0, min(1.0, faithfulness))  # Giới hạn trong [0.0, 1.0]

    def evaluate_relevance(self, answer: str, question: str) -> float:
        """
        Đo mức độ câu trả lời liên quan đến câu hỏi.

        Heuristic:
            relevance = |answer_tokens ∩ question_tokens| / |question_tokens|
            Giới hạn trong [0.0, 1.0]. Trả về 1.0 nếu câu hỏi rỗng.

        Trả về:
            Số thực trong [0.0, 1.0]
        """
        answer_tokens = _tokenize(answer)
        question_tokens = _tokenize(question)
        if not question_tokens:
            return 1.0
        intersection = answer_tokens.intersection(question_tokens)
        relevance = len(intersection) / len(question_tokens)
        return max(0.0, min(1.0, relevance))  # Giới hạn trong [0.0, 1.0]

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        """
        Đo mức độ câu trả lời bao phủ câu trả lời kỳ vọng.

        Heuristic:
            completeness = |answer_tokens ∩ expected_tokens| / |expected_tokens|
            Giới hạn trong [0.0, 1.0]. Trả về 1.0 nếu câu trả lời kỳ vọng rỗng.

        Trả về:
            Số thực trong [0.0, 1.0]
        """
        answer_tokens = _tokenize(answer)
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        completeness = len(answer_tokens.intersection(expected_tokens)) / len(
            expected_tokens
        )
        return max(0.0, min(1.0, completeness))  # Giới hạn trong [0.0, 1.0]

    # -----------------------------------------------------------------------
    # Tác vụ 2b — Chỉ số phía truy xuất (đánh giá bước LẤY-NGỮ-CẢNH)
    # -----------------------------------------------------------------------
    # Theo bài giảng (quy trình RAG): Context Recall → Context Precision →
    #   Faithfulness → Answer Relevancy. Hai chỉ số dưới đây chấm BỘ TRUY XUẤT,
    #   hoạt động trên DANH SÁCH các đoạn (thứ tự = thứ hạng từ bộ truy xuất).
    # -----------------------------------------------------------------------

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        """Context Recall — mức độ HỢP của các đoạn được truy xuất bao phủ
        câu trả lời kỳ vọng.

        Heuristic:
            union_tokens = ⋃ _tokenize(chunk) for chunk in contexts
            recall = |expected_tokens ∩ union_tokens| / |expected_tokens|
            Giới hạn trong [0.0, 1.0]. Trả về 1.0 nếu câu trả lời kỳ vọng rỗng.

        Recall thấp => bộ truy xuất đã bỏ sót bằng chứng cần cho câu trả lời.
        """
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        union_tokens = set()
        for chunk in contexts:
            union_tokens.update(_tokenize(chunk))
        recall = len(expected_tokens.intersection(union_tokens)) / len(expected_tokens)
        return max(0.0, min(1.0, recall))

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        """Context Precision — Average Precision có xét thứ hạng (AP@K).

        Một đoạn được xem là liên quan khi tỷ lệ token kỳ vọng xuất hiện trong
        đoạn đạt ``relevance_threshold``. Các đoạn liên quan xuất hiện sớm sẽ
        nhận Precision@k cao hơn. Trả về 1.0 nếu expected rỗng và 0.0 nếu
        không có contexts hoặc không có đoạn liên quan.
        """
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        if not contexts:
            return 0.0

        relevance_flags: list[bool] = []
        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            overlap = len(chunk_tokens.intersection(expected_tokens))
            relevance = overlap / len(expected_tokens)
            relevance_flags.append(relevance >= relevance_threshold)

        total_relevant = sum(relevance_flags)
        if total_relevant == 0:
            return 0.0

        relevance_seen = 0
        precision_sum = 0.0
        for k, is_relevant in enumerate(relevance_flags, start=1):
            if is_relevant:
                relevance_seen += 1
                precision_sum += relevance_seen / k

        context_precision = precision_sum / total_relevant
        return max(0.0, min(1.0, context_precision))

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
        contexts: list[str] | None = None,
    ) -> EvalResult:
        """Chạy các answer metrics và optional retrieval metrics.

        ``passed`` chỉ dựa trên ba answer metrics. Hai retrieval metrics được
        lưu để chẩn đoán và không tham gia ``overall_score()``.
        """
        faithfulness = self.evaluate_faithfulness(answer, context)
        relevance = self.evaluate_relevance(answer, question)
        completeness = self.evaluate_completeness(answer, expected)

        passed = faithfulness >= 0.5 and relevance >= 0.5 and completeness >= 0.5
        failure_type: str | None = None
        if not passed:
            if faithfulness < 0.3:
                failure_type = "hallucination"
            elif relevance < 0.3:
                failure_type = "irrelevant"
            elif completeness < 0.3:
                failure_type = "incomplete"
            else:
                failure_type = "off_topic"
        context_recall: float | None = None
        context_precision: float | None = None
        if contexts is not None:
            context_recall = self.evaluate_context_recall(contexts, expected)
            context_precision = self.evaluate_context_precision(contexts, expected)
        qa_pair = QAPair(question=question, expected_answer=expected, context=context)
        return EvalResult(
            qa_pair=qa_pair,
            actual_answer=answer,
            faithfulness=faithfulness,
            relevance=relevance,
            completeness=completeness,
            passed=passed,
            failure_type=failure_type,
            context_recall=context_recall,
            context_precision=context_precision,
        )


# ---------------------------------------------------------------------------
# Hàm hỗ trợ xếp hạng lại (dùng trong Bài tập 3.5 — tăng Context Precision)
# ---------------------------------------------------------------------------


def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    """Bộ xếp hạng lại theo từ vựng tối giản: sắp xếp các đoạn theo độ chồng lấp
    từ với truy vấn, đoạn chồng lấp nhiều nhất đứng trước. Đây là phương án thay thế
    đơn giản cho một bộ xếp hạng lại cross-encoder thực tế.

    Đưa các đoạn liên quan lên đầu sẽ tăng Context Precision có xét thứ hạng
    mà KHÔNG thay đổi tập kết quả truy xuất.

    Gợi ý: sorted(contexts, key=lambda c: len(_tokenize(c) & _tokenize(query)),
                  reverse=True)
    """
    query_tokens = _tokenize(query)
    return sorted(
        contexts,
        key=lambda context: len(_tokenize(context).intersection(query_tokens)),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Tác vụ 3 — LLM làm giám khảo
# ---------------------------------------------------------------------------
# Theo bài giảng:
#   - LLM giám khảo nhận: câu hỏi + câu trả lời của tác tử + câu trả lời tham chiếu + rubric
#   - Giám khảo trả về: Điểm 1-5 + Lý do
#   - Thực hành tốt: nhiều giám khảo, ngẫu nhiên hóa thứ tự, hiệu chỉnh theo con người
#   - Thiên kiến: vị trí, dài dòng, ưu tiên bản thân
#   - Mẫu rubric:
#       5 = Chính xác, đầy đủ, trích dẫn tốt
#       4 = Phần lớn chính xác, còn thiếu sót nhỏ
#       3 = Đúng một phần, có một số lỗi
#       2 = Có lỗi đáng kể hoặc thiếu thông tin
#       1 = Sai hoặc không liên quan
# ---------------------------------------------------------------------------


class LLMJudge:
    """
    Dùng một LLM để chấm câu trả lời của AI theo rubric.
    """

    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        """Gửi câu trả lời cho LLM chấm và đọc điểm từ kết quả JSON.

        Nếu kết quả không đúng dạng mong đợi, mỗi tiêu chí nhận điểm mặc định
        là 0.5. Mọi điểm hợp lệ đều được giữ trong khoảng từ 0.0 đến 1.0.
        """
        rubric_str = "\n".join(f"{k}: {v}" for k, v in rubric.items())
        prompt = f"""
You are an AI judge. Score the following answer based on the rubric below.
Question:
{question}
Answer:
{answer}
Rubric:
{rubric_str}
Return only a JSON object mapping each rubric criterion to a score
between 0.0 and 1.0.
""".strip()
        raw_response = self.judge_llm_fn(prompt)
        default_scores = {criterion: 0.5 for criterion in rubric}
        scores = default_scores.copy()

        try:
            parsed = json.loads(raw_response)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None

        if isinstance(parsed, dict):
            score_data = parsed.get("scores", parsed)

            if isinstance(score_data, dict):
                for criterion in rubric:
                    try:
                        value = float(score_data.get(criterion, 0.5))
                    except (TypeError, ValueError):
                        value = 0.5

                    scores[criterion] = max(0.0, min(1.0, value))

        return {
            "scores": scores,
            "reasoning": raw_response,
        }

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        """Kiểm tra dấu hiệu ưu tiên câu đầu, chấm quá dễ hoặc quá khó."""
        all_scores: list[float] = []
        result_averages: list[float] = []

        for result in scores_batch:
            if not isinstance(result, dict):
                continue

            scores = result.get("scores", {})
            if not isinstance(scores, dict):
                continue

            numeric_scores = [
                float(value)
                for value in scores.values()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            ]

            if numeric_scores:
                all_scores.extend(numeric_scores)
                result_averages.append(sum(numeric_scores) / len(numeric_scores))

        if all_scores:
            overall_average = sum(all_scores) / len(all_scores)
            leniency_bias = overall_average > 0.8
            severity_bias = overall_average < 0.3
        else:
            leniency_bias = False
            severity_bias = False

        positional_bias = False

        if len(result_averages) >= 2:
            later_average = sum(result_averages[1:]) / len(result_averages[1:])
            positional_bias = result_averages[0] > later_average + 0.05

        return {
            "positional_bias": positional_bias,
            "leniency_bias": leniency_bias,
            "severity_bias": severity_bias,
        }


# ---------------------------------------------------------------------------
# Tác vụ 4 — Trình chạy đo chuẩn
# ---------------------------------------------------------------------------
# Theo bài giảng:
#   - Tích hợp CI/CD: Framework + CI/CD = cổng chất lượng tự động
#   - Tác tử có faithfulness < 0.7 → không được triển khai
#   - Hồi quy = chỉ số giảm > 0.05 so với đường cơ sở
#   - Kích hoạt: mỗi bản phát hành mã, mỗi lần đổi prompt, trước khi demo/ra mắt
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    """
    Chạy một bộ đo chuẩn đánh giá đầy đủ.
    """

    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        """
        Chạy tất cả cặp hỏi-đáp qua tác tử và đánh giá từng kết quả.

        Tham số:
            qa_pairs:   Danh sách các đối tượng QAPair.
            agent_fn:   Hàm str → str (hàm trả lời của tác tử).
            evaluator:  Một thực thể RAGASEvaluator.

        Trả về:
            Danh sách EvalResult, mỗi qa_pair tương ứng một kết quả.
        """

        results: list[EvalResult] = []

        for pair in qa_pairs:
            actual_answer = agent_fn(pair.question)

            result = evaluator.run_full_eval(
                answer=actual_answer,
                question=pair.question,
                context=pair.context,
                expected=pair.expected_answer,
                contexts=pair.retrieved_contexts,
            )
            result.qa_pair = pair
            results.append(result)

        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        """
        Tạo báo cáo tổng hợp từ các kết quả đánh giá.

        Trả về:
            {
                "total":            int,
                "passed":           int,
                "pass_rate":        float,  # số đạt / tổng số
                "avg_faithfulness": float,
                "avg_relevance":    float,
                "avg_completeness": float,
                "avg_context_recall": float | None,
                "avg_context_precision": float | None,
                "failure_types":    dict[str, int],  # loại → số lượng
            }

        Chỉ tính trung bình các điểm truy xuất khác None. Trả về None cho
        trung bình truy xuất nếu không kết quả nào chứa chỉ số đó.
        """
        total = len(results)

        if total == 0:
            return {
                "total": 0,
                "passed": 0,
                "pass_rate": 0.0,
                "avg_faithfulness": 0.0,
                "avg_relevance": 0.0,
                "avg_completeness": 0.0,
                "avg_context_recall": None,
                "avg_context_precision": None,
                "failure_types": {},
            }

        passed_count = sum(1 for result in results if result.passed)

        failure_types: dict[str, int] = {}

        for result in results:
            if result.failure_type is not None:
                failure_types[result.failure_type] = (
                    failure_types.get(result.failure_type, 0) + 1
                )

        recall_scores = [
            result.context_recall
            for result in results
            if result.context_recall is not None
        ]

        precision_scores = [
            result.context_precision
            for result in results
            if result.context_precision is not None
        ]

        return {
            "total": total,
            "passed": passed_count,
            "pass_rate": passed_count / total,
            "avg_faithfulness": (
                sum(result.faithfulness for result in results) / total
            ),
            "avg_relevance": (sum(result.relevance for result in results) / total),
            "avg_completeness": (
                sum(result.completeness for result in results) / total
            ),
            "avg_context_recall": (
                sum(recall_scores) / len(recall_scores) if recall_scores else None
            ),
            "avg_context_precision": (
                sum(precision_scores) / len(precision_scores)
                if precision_scores
                else None
            ),
            "failure_types": failure_types,
        }

    def run_regression(self, new_results: list, baseline_results: list) -> dict:
        """So sánh kết quả đánh giá mới với đường cơ sở.

        Hồi quy xảy ra khi trung bình một chỉ số giảm hơn 0.05 so với đường cơ sở.

        Tham số:
            new_results: Danh sách các EvalResult (lần chạy hiện tại)
            baseline_results: Danh sách các EvalResult (tham chiếu/đường cơ sở)

        Trả về:
            Từ điển có các khóa:
              - 'new_avg_faithfulness': float
              - 'new_avg_relevance': float
              - 'new_avg_completeness': float
              - 'baseline_avg_faithfulness': float
              - 'baseline_avg_relevance': float
              - 'baseline_avg_completeness': float
              - 'regressions': list[str] — tên các chỉ số bị hồi quy
              - 'passed': bool — True nếu không có hồi quy

        """

        def average(results: list, attribute: str) -> float:
            if not results:
                return 0.0

            return sum(getattr(result, attribute) for result in results) / len(results)

        metrics = [
            "faithfulness",
            "relevance",
            "completeness",
        ]

        new_averages = {metric: average(new_results, metric) for metric in metrics}

        baseline_averages = {
            metric: average(baseline_results, metric) for metric in metrics
        }

        regressions = [
            metric
            for metric in metrics
            if new_averages[metric] < baseline_averages[metric] - 0.05
        ]

        return {
            "new_avg_faithfulness": new_averages["faithfulness"],
            "new_avg_relevance": new_averages["relevance"],
            "new_avg_completeness": new_averages["completeness"],
            "baseline_avg_faithfulness": baseline_averages["faithfulness"],
            "baseline_avg_relevance": baseline_averages["relevance"],
            "baseline_avg_completeness": baseline_averages["completeness"],
            "regressions": regressions,
            "passed": len(regressions) == 0,
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        """
        Trả về các EvalResult có ít nhất một điểm thấp hơn ngưỡng.

        Tham số:
            results:   Danh sách đầy đủ các EvalResult.
            threshold: Điểm tối thiểu chấp nhận được cho mỗi chỉ số.

        Trả về:
            Danh sách các EvalResult không đạt.
        """
        return [
            result
            for result in results
            if (
                result.faithfulness < threshold
                or result.relevance < threshold
                or result.completeness < threshold
            )
        ]


# ---------------------------------------------------------------------------
# Tác vụ 5 — Bộ phân tích lỗi
# ---------------------------------------------------------------------------
# Theo bài giảng:
#   Phân loại lỗi:
#     - hallucination: bịa thông tin → rào chắn faithfulness yếu
#     - irrelevant: không giải quyết câu hỏi → prompt mơ hồ
#     - incomplete: bỏ sót thông tin → cửa sổ ngữ cảnh nhỏ, truy xuất thiếu
#     - off_topic: trả lời chủ đề khác → phát hiện ý định sai
#     - refusal: từ chối khi nên trả lời → rào chắn quá chặt
#
#   Phương pháp 5 Whys: hỏi "Tại sao?" liên tục cho đến nguyên nhân gốc rễ
#   Phân cụm lỗi: sửa một nguyên nhân gốc rễ để xử lý nhiều lỗi cùng lúc
#   Cải tiến liên tục: Đánh giá → Phân tích → Cải tiến → Bổ sung → Lặp lại
# ---------------------------------------------------------------------------


class FailureAnalyzer:
    """
    Phân tích các kết quả đánh giá không đạt để tìm mẫu lỗi và đề xuất cách sửa.
    """

    def categorize_failures(self, failures: list[EvalResult]) -> dict[str, int]:
        """
        Đếm lỗi theo failure_type.

        Trả về:
            Từ điển ánh xạ failure_type → số lượng.
            Ví dụ: {"hallucination": 3, "irrelevant": 2, "incomplete": 5}
        """
        categories: dict[str, int] = {}

        for failure in failures:
            failure_type = failure.failure_type or "unknown"
            categories[failure_type] = categories.get(failure_type, 0) + 1

        return categories

    def find_root_cause(self, failure: EvalResult) -> str:
        """
        Đề xuất nguyên nhân gốc rễ cho một lỗi dựa trên các điểm số.

        Trả về một trong các chuỗi sau dựa trên điểm thấp nhất:
            "Context is missing or irrelevant — improve retrieval"
            "Answer does not address the question — improve prompt clarity"
            "Answer is missing key information — increase context window or improve generation"
            "Multiple issues detected — review full pipeline"
        """
        scores = {
            "faithfulness": failure.faithfulness,
            "relevance": failure.relevance,
            "completeness": failure.completeness,
        }

        lowest_score = min(scores.values())

        lowest_metrics = [
            metric for metric, score in scores.items() if score == lowest_score
        ]

        if len(lowest_metrics) != 1:
            return "Multiple issues detected — review full pipeline"

        lowest_metric = lowest_metrics[0]

        if lowest_metric == "faithfulness":
            return "Context is missing or irrelevant — improve retrieval"

        if lowest_metric == "relevance":
            return "Answer does not address the question — improve prompt clarity"

        return (
            "Answer is missing key information — "
            "increase context window or improve generation"
        )

    def generate_improvement_log(self, failures: list, suggestions: list[str]) -> str:
        """Tạo bảng Markdown ghi lại lỗi và hành động cải tiến.

        Định dạng:
        | Failure ID | Type | Root Cause | Suggested Fix | Status |
        |------------|------|------------|---------------|--------|
        | F001       | ...  | ...        | ...           | Open   |

        Tham số:
            failures: Danh sách các EvalResult có passed=False
            suggestions: Danh sách đề xuất (mỗi lỗi một đề xuất, có thể ngắn hơn danh sách lỗi)

        Trả về:
            Chuỗi bảng Markdown, mỗi lỗi một hàng. Trạng thái luôn là "Open".

        """
        header = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|",
        ]

        rows: list[str] = []

        for index, failure in enumerate(failures, start=1):
            failure_id = f"F{index:03d}"
            failure_type = failure.failure_type or "Unknown"
            root_cause = self.find_root_cause(failure)

            if index - 1 < len(suggestions):
                suggested_fix = suggestions[index - 1]
            else:
                suggested_fix = "Review failure and define corrective action"

            rows.append(
                f"| {failure_id} | {failure_type} | "
                f"{root_cause} | {suggested_fix} | Open |"
            )

        return "\n".join(header + rows)

    def generate_improvement_suggestions(self, failures: list[EvalResult]) -> list[str]:
        """
        Tạo danh sách đề xuất cải tiến theo thứ tự ưu tiên dựa trên các mẫu lỗi.

        Mỗi đề xuất phải cụ thể và có thể thực hiện được.

        Ví dụ:
            "Increase chunk size in RAG pipeline to reduce context fragmentation"
            "Add few-shot examples showing complete answers to improve completeness"
            "Implement hallucination checker to filter unsupported claims"

        Trả về:
            Danh sách ít nhất 3 đề xuất (hoặc ít hơn nếu danh sách lỗi rỗng).
        """
        if not failures:
            return []

        categories = self.categorize_failures(failures)
        suggestions: list[str] = []

        if categories.get("hallucination", 0) > 0:
            suggestions.append(
                "Add claim-to-context validation to filter unsupported statements"
            )

        if categories.get("irrelevant", 0) > 0:
            suggestions.append(
                "Improve intent detection and clarify the answer-generation prompt"
            )

        if categories.get("incomplete", 0) > 0:
            suggestions.append(
                "Improve retrieval coverage and add answer-completeness checks"
            )

        if categories.get("off_topic", 0) > 0:
            suggestions.append("Add domain routing and explicit out-of-scope handling")

        if categories.get("refusal", 0) > 0:
            suggestions.append("Review guardrail rules to reduce unnecessary refusals")

        fallback_suggestions = [
            "Add representative failures to the regression golden dataset",
            "Review the lowest-scoring cases and inspect retrieved evidence",
            "Calibrate evaluation thresholds against human labels",
        ]

        for suggestion in fallback_suggestions:
            if len(suggestions) >= 3:
                break

            if suggestion not in suggestions:
                suggestions.append(suggestion)

        return suggestions


# ---------------------------------------------------------------------------
# Điểm vào để kiểm thử thủ công
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Bộ dữ liệu chuẩn mẫu (bản thu nhỏ — dùng 20 cặp trong bài lab thực tế)
    # Theo bài giảng: lấy mẫu phân tầng = 5 Dễ + 7 Trung bình + 5 Khó + 3 Đối kháng
    qa_pairs = [
        # Dễ — tra cứu thông tin thực tế
        QAPair(
            question="What is RAG?",
            expected_answer="RAG stands for Retrieval-Augmented Generation, which combines retrieval with text generation.",
            context="RAG is a technique that retrieves relevant documents and uses them to ground LLM generation.",
            metadata={"difficulty": "easy", "category": "definition"},
        ),
        QAPair(
            question="What is the capital of France?",
            expected_answer="Paris is the capital of France.",
            context="France is a country in Western Europe. Its capital city is Paris.",
            metadata={"difficulty": "easy", "category": "factual"},
        ),
        # Trung bình — suy luận nhiều bước
        QAPair(
            question="Explain backpropagation and why it matters for training",
            expected_answer="Backpropagation is an algorithm for training neural networks by computing gradients efficiently, enabling deep learning models to learn from errors.",
            context="Neural networks learn through gradient descent. Backpropagation efficiently computes these gradients layer by layer.",
            metadata={"difficulty": "medium", "category": "explanation"},
        ),
        # Khó — mơ hồ
        QAPair(
            question="Should I use RAG or fine-tuning for my chatbot?",
            expected_answer="It depends on the use case: RAG is better for frequently updated knowledge, fine-tuning for consistent style/behavior. Consider cost, latency, and data freshness.",
            context="RAG retrieves external documents at inference time. Fine-tuning modifies model weights during training.",
            metadata={"difficulty": "hard", "category": "comparison"},
        ),
        # Đối kháng — ngoài phạm vi
        QAPair(
            question="What is the meaning of life?",
            expected_answer="This question is outside the scope of this system. I can help with AI and technology questions.",
            context="This is an AI assistant specialized in technology topics.",
            metadata={"difficulty": "adversarial", "category": "out_of_scope"},
        ),
    ]

    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()

    def mock_agent(question: str) -> str:
        """Tác tử giả lập đơn giản để kiểm thử. Hãy thay bằng tác tử thực tế của bạn."""
        return f"Based on my knowledge: {question[:30]}... The answer involves key concepts."

    # Chạy đo chuẩn
    results = runner.run(qa_pairs, mock_agent, evaluator)
    report = runner.generate_report(results)
    print("=== Benchmark Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")

    # Xác định và phân tích lỗi
    failures = runner.identify_failures(results, threshold=0.5)
    print(f"\n=== Failures ({len(failures)}) ===")
    analyzer = FailureAnalyzer()

    # Phân loại (theo bài giảng: phân cụm trước khi sửa)
    categories = analyzer.categorize_failures(failures)
    print("Failure Categories:", categories)

    # Nguyên nhân gốc rễ của từng lỗi (theo bài giảng: 5 Whys)
    for f in failures:
        cause = analyzer.find_root_cause(f)
        print(f"  Root cause: {cause}")

    # Đề xuất cải tiến (theo bài giảng: vòng lặp cải tiến liên tục)
    suggestions = analyzer.generate_improvement_suggestions(failures)
    print("\nImprovement Suggestions:")
    for s in suggestions:
        print(f"  - {s}")

    # Tạo nhật ký cải tiến (bảng Markdown)
    log = analyzer.generate_improvement_log(failures, suggestions)
    print("\n=== Improvement Log ===")
    print(log)
