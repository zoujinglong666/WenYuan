"""离线检索评估集：固定语料 + 固定问题，跑出召回率 / 引用命中率 / 拒答闸门通过率。

目的：调 RRF_K / RETRIEVE_TOP_K / RERANK_TOP_K 等参数时有回归数字，
不再靠「感觉检索变好了」。语料与问题内置且确定性（self-contained），
不依赖真实 data/local_store 的内容。

用法（项目根目录）：
    python tests/eval_retrieval.py                     # 默认参数跑一遍
    python tests/eval_retrieval.py --rrf-k 30          # 试参数，看指标变化
    python tests/eval_retrieval.py --gate 0.85         # 收紧回归门槛

退出码：指标未达门槛返回 1，可直接挂 CI / 提交前自检。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

# 允许 `python tests/eval_retrieval.py` 从项目根目录直接跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# 内置语料：10 份确定性文档，每份带独有编号/专有名词（BM25 可精确命中），
# 同时内容语义完整（向量可意会）。文件名即评估的期望命中目标。
# ---------------------------------------------------------------------------

CORPUS: dict[str, str] = {
    "差旅费报销管理制度.txt": (
        "差旅费报销管理制度（制度编号 CX-2024-018）\n"
        "员工出差实行事前审批制，出差前须在 OA 提交出差申请并注明事由、目的地与天数。\n"
        "交通与住宿实报实销，单次差旅报销上限为 5000 元，超出部分不予受理。\n"
        "餐饮补贴按每天 100 元包干，不需要提供发票。报销须在返程后 15 个工作日内提交，"
        "附发票原件与行程单，部门负责人签字后交财务部审核打款。\n"
    ),
    "员工考勤与请假管理办法.txt": (
        "员工考勤与请假管理办法（制度编号 KQ-2024-031）\n"
        "工作日实行上下班打卡制度，迟到超过 30 分钟记为旷工半天。\n"
        "年假按工龄核定：入职满一年享受 5 天年假，满十年享受 15 天年假。\n"
        "病假须提供二级以上医院开具的病假条，事假一天以内由部门主管批准，"
        "三天以上须经人力资源部审批并备案。\n"
    ),
    "采购审批权限指引.txt": (
        "采购审批权限指引（制度编号 CG-2023-101）\n"
        "单笔采购金额 5000 元以下由部门负责人直接审批；5000 元至 5 万元须经分管副总审批；"
        "超过 5 万元的采购必须报总裁审批并组织三家以上供应商比价。\n"
        "所有采购须先在系统中创建采购单，验收合格后凭发票与入库单办理付款。\n"
    ),
    "数据安全与密码策略.txt": (
        "数据安全与密码策略（制度编号 SEC-2025-007）\n"
        "所有系统账号密码长度不得少于 12 位，必须同时包含大小写字母、数字与特殊字符。\n"
        "密码每 90 天强制更换一次，且不得与最近三次使用过的密码重复。\n"
        "核心业务系统的密码必须加密存储，禁止在代码、聊天工具或纸质便签中明文记录。"
        "发现账号异常登录应立即修改密码并上报安全组。\n"
    ),
    "新员工入职手册.txt": (
        "新员工入职手册\n"
        "入职第一天请携带身份证复印件、学历证书原件、体检报告与前单位离职证明。\n"
        "试用期统一为 6 个月，试用期考核合格后转正。\n"
        "每位新员工配备一名导师，负责岗位职责培训；工牌与门禁卡由行政部在入职当天发放，"
        "邮箱与企业 IM 账号由 IT 部在入职前开通。\n"
    ),
    "服务器运维值班条例.txt": (
        "服务器运维值班条例（制度编号 OPS-2024-042）\n"
        "运维实行 7x24 值班轮换制，值班表每周由运维组长排定并公示。\n"
        "生产环境告警必须在 15 分钟内响应确认，重大故障 30 分钟内上报技术负责人并启动应急预案。\n"
        "值班人员交接班须书面记录未闭环事项，禁止擅离职守或私自转岗。\n"
    ),
    "合同审批流程说明.txt": (
        "合同审批流程说明（制度编号 HT-2024-056）\n"
        "对外合同签订前须经业务部门起草、法务部合规审核、财务部测算预算，三方会签完成后，"
        "由分管领导终审。\n"
        "合同用印须提交用印申请单，附合同终稿与会签记录，经行政部核验后加盖公章。"
        "未经审批的合同文本一律不得对外发送。\n"
    ),
    "客户投诉处理规范.txt": (
        "客户投诉处理规范（制度编号 TS-2024-012）\n"
        "客户投诉按影响程度分为三级：一级投诉 2 小时内响应，二级投诉 8 小时内响应，"
        "一般投诉须在 24 小时内响应并给出处理方案。\n"
        "投诉处理遵循先安抚、再定位、后补偿的原则，补偿金额超过 1 万元须报客服总监批准。\n"
        "所有投诉须录入投诉台账并在闭环后回访确认满意度。\n"
    ),
    "年度绩效考评方案.txt": (
        "年度绩效考评方案\n"
        "绩效考评采用 KPI 目标打分与 360 度环评相结合的方式，权重分别为 70% 与 30%。\n"
        "考评结果分为 S/A/B/C/D 五级，S 级比例不超过团队人数的 10%。\n"
        "考评结果与年终奖、调薪及晋升挂钩，C 级及以下人员须制定绩效改进计划。\n"
    ),
    "办公用品申领规定.txt": (
        "办公用品申领规定\n"
        "办公用品由行政部统一采购与发放，员工通过 OA 系统提交申领单，部门负责人审批后"
        "凭单据到行政库房领取。\n"
        "每人每月申领额度为 100 元，超出额度须说明理由并经行政部特批。\n"
        "打印纸、签字笔等高频耗材按季度集中配发，贵重设备领用须登记资产编号。\n"
    ),
}

# ---------------------------------------------------------------------------
# 评估问题集。kind 含义：
#   exact    —— 问题包含文档中的关键实体/数字/编号（BM25 强项）
#   semantic —— 问题是语义改写，不含原文原词（向量强项）
#   negative —— 知识库外问题（应当被拒答闸门拦住，而不是硬答）
# ---------------------------------------------------------------------------

QUESTIONS: list[dict[str, str]] = [
    # --- exact：精确词命中 ---
    {"kind": "exact", "expect": "差旅费报销管理制度", "q": "单次差旅报销的上限金额是多少？"},
    {"kind": "exact", "expect": "差旅费报销管理制度", "q": "CX-2024-018 这个制度里餐饮补贴怎么算？"},
    {"kind": "exact", "expect": "员工考勤与请假管理办法", "q": "入职满十年的员工有几天年假？"},
    {"kind": "exact", "expect": "采购审批权限指引", "q": "采购金额超过5万元需要谁审批？"},
    {"kind": "exact", "expect": "数据安全与密码策略", "q": "系统密码多久需要更换一次？"},
    {"kind": "exact", "expect": "新员工入职手册", "q": "新员工试用期是多长时间？"},
    {"kind": "exact", "expect": "服务器运维值班条例", "q": "生产环境告警要求多少分钟内响应？"},
    {"kind": "exact", "expect": "合同审批流程说明", "q": "合同用印需要提交什么申请？"},
    {"kind": "exact", "expect": "客户投诉处理规范", "q": "二级投诉要求几小时内响应？"},
    {"kind": "exact", "expect": "办公用品申领规定", "q": "每人每月办公用品申领额度是多少？"},

    # --- semantic：语义改写，不含文档原词 ---
    {"kind": "semantic", "expect": "差旅费报销管理制度", "q": "我下周要去外地出差，路上和住酒店的钱怎么找公司报？"},
    {"kind": "semantic", "expect": "员工考勤与请假管理办法", "q": "家里有急事想请几天假，应该找谁批准？"},
    {"kind": "semantic", "expect": "采购审批权限指引", "q": "部门想买一批新电脑，大概要六万块，报告要给谁签字？"},
    {"kind": "semantic", "expect": "数据安全与密码策略", "q": "担心工作账号被盗，公司对设置密码有什么要求吗？"},
    {"kind": "semantic", "expect": "新员工入职手册", "q": "明天第一天上班，需要带哪些材料去办理手续？"},
    {"kind": "semantic", "expect": "服务器运维值班条例", "q": "半夜系统挂了，报警之后多久会有人来处理？"},
    {"kind": "semantic", "expect": "合同审批流程说明", "q": "和客户谈好的合同，盖章之前要经过哪些环节？"},
    {"kind": "semantic", "expect": "客户投诉处理规范", "q": "客户在电话里发火了说要投诉，我们该怎么处理？"},
    {"kind": "semantic", "expect": "年度绩效考评方案", "q": "年终考评是怎么打分的，评分占比如何？"},
    {"kind": "semantic", "expect": "办公用品申领规定", "q": "打印纸用完了，怎么去领一批新的？"},

    # --- negative：知识库外，期望被闸门拦下 ---
    {"kind": "negative", "expect": "", "q": "公司股票现在多少钱一股？"},
    {"kind": "negative", "expect": "", "q": "怎么办理出国探亲签证？"},
    {"kind": "negative", "expect": "", "q": "明天天气预报说会下雨吗？"},
    {"kind": "negative", "expect": "", "q": "附近有什么好吃的火锅店推荐？"},
]

GATE_DEFAULT = 0.80  # top3 命中率回归门槛（exact + semantic 合并）


def main() -> int:
    ap = argparse.ArgumentParser(description="RAG 检索离线评估")
    ap.add_argument("--retrieve-top-k", type=int, help="覆盖 RETRIEVE_TOP_K（召回候选数）")
    ap.add_argument("--rerank-top-k", type=int, help="覆盖 RERANK_TOP_K（精排返回数）")
    ap.add_argument("--rrf-k", type=int, help="覆盖 RRF_K（融合常数）")
    ap.add_argument("--gate", type=float, default=GATE_DEFAULT, help=f"top3 命中率门槛（默认 {GATE_DEFAULT}）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args()

    # 参数覆盖必须在导入 app.config 之前写入环境（pydantic-settings 导入时读取）
    overrides = {
        "RETRIEVE_TOP_K": args.retrieve_top_k,
        "RERANK_TOP_K": args.rerank_top_k,
        "RRF_K": args.rrf_k,
    }
    for k, v in overrides.items():
        if v is not None:
            os.environ[k] = str(v)

    tmp = tempfile.mkdtemp(prefix="wenyuan-eval-")
    os.environ["LOCAL_STORE_DIR"] = str(Path(tmp) / "store")
    os.environ["ADMIN_INITIAL_PASSWORD"] = "eval-admin-pw1"

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        # 登录管理员
        r = client.post(
            "/v1/auth/login",
            json={"username": "admin", "password": "eval-admin-pw1"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 建语料库
        t0 = time.perf_counter()
        doc_ids: dict[str, str] = {}
        for filename, content in CORPUS.items():
            r = client.post(
                "/v1/documents",
                files={"file": (filename, content.encode("utf-8"), "text/plain")},
                data={"acl_ids": "public"},
                headers=headers,
            )
            assert r.status_code == 200, f"{filename}: {r.text}"
            doc_ids[filename] = r.json()["doc_id"]
        ingest_s = time.perf_counter() - t0

        # 逐题检索并评估
        rows = []
        t0 = time.perf_counter()
        for item in QUESTIONS:
            r = client.post(
                "/v1/search",
                json={"query": item["q"], "top_k": 8},
                headers=headers,
            )
            assert r.status_code == 200, f"search 失败: {r.text}"
            hits = r.json()["hits"]
            filenames = [h["filename"] for h in hits]
            best_score = max((h["rerank_score"] or 0.0 for h in hits), default=None)
            scores = [h["rerank_score"] or 0.0 for h in hits]

            if item["kind"] == "negative":
                # 诊断项（不参与回归门槛）：精排分数层对「弱相关 vs 离题」的区分度有限
                # （实测正例最低 0.18 / 负例最高 0.38 重叠），负例防线主力是生成层
                # 的 grounding 提示词（ANSWER_SYSTEM_PROMPT 规则 2），此处只观测
                # 分数层的拦截水平，为将来的校准留基线。
                best_rank = None
                passed = True
                score_gate_hit = best_score is not None and best_score < settings.rerank_min_score
            else:
                stem = Path(item["expect"]).stem
                best_rank = next(
                    (i + 1 for i, f in enumerate(filenames) if stem in f), None
                )
                passed = best_rank is not None
                score_gate_hit = False

            rows.append(
                {
                    **item,
                    "best_rank": best_rank,
                    "best_score": best_score,
                    "filenames": filenames,
                    "passed": passed,
                    "score_gate_hit": score_gate_hit,
                }
            )
        search_s = time.perf_counter() - t0

    # ---- 汇总指标 ----
    def hit_at(rows: list[dict], n: int) -> float:
        vals = [r["best_rank"] for r in rows if r["kind"] != "negative"]
        return sum(1 for v in vals if v is not None and v <= n) / len(vals)

    exact_rows = [r for r in rows if r["kind"] == "exact"]
    sem_rows = [r for r in rows if r["kind"] == "semantic"]
    neg_rows = [r for r in rows if r["kind"] == "negative"]
    ranked_rows = exact_rows + sem_rows

    mrr = sum(1.0 / r["best_rank"] for r in ranked_rows if r["best_rank"]) / len(ranked_rows)
    neg_gate = sum(1 for r in neg_rows if r["score_gate_hit"]) / len(neg_rows)
    top3_overall = hit_at(ranked_rows, 3)

    result = {
        "params": {
            "retrieve_top_k": settings.retrieve_top_k,
            "rerank_top_k": settings.rerank_top_k,
            "rrf_k": settings.rrf_k,
            "rerank_min_score": settings.rerank_min_score,
        },
        "corpus_docs": len(CORPUS),
        "questions": len(QUESTIONS),
        "top1_hit": hit_at(ranked_rows, 1),
        "top3_hit": top3_overall,
        "top8_hit": hit_at(ranked_rows, 8),
        "mrr": round(mrr, 4),
        "negative_score_gate": round(neg_gate, 4),
        "by_kind": {
            k: {
                "top1": hit_at([r for r in rows if r["kind"] == k], 1),
                "top3": hit_at([r for r in rows if r["kind"] == k], 3),
                "count": len([r for r in rows if r["kind"] == k]),
            }
            for k in ("exact", "semantic")
        },
        "gate": args.gate,
        "gate_passed": top3_overall >= args.gate,
        "timings": {"ingest_s": round(ingest_s, 1), "search_s": round(search_s, 1)},
    }

    if args.json:
        import json

        result["detail"] = [
            {k: v for k, v in r.items() if k != "filenames"} | {"hits": r["filenames"][:3]}
            for r in rows
        ]
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== 问源检索离线评估（语料 {len(CORPUS)} 篇 / 问题 {len(QUESTIONS)} 条）===")
        print(f"参数: RRF_K={settings.rrf_k} RETRIEVE_TOP_K={settings.retrieve_top_k} "
              f"RERANK_TOP_K={settings.rerank_top_k} RERANK_MIN_SCORE={settings.rerank_min_score}")
        print(f"\n Top1 命中率: {result['top1_hit']:.1%}   Top3 命中率: {result['top3_hit']:.1%}   "
              f"Top8 命中率(引用池): {result['top8_hit']:.1%}")
        print(f" MRR: {result['mrr']:.3f}   检索分数层负例拦截率(诊断): {result['negative_score_gate']:.0%}"
              f"（防线主力为生成层 grounding）")
        print(f" 分项: exact top3 {result['by_kind']['exact']['top3']:.0%} / "
              f"semantic top3 {result['by_kind']['semantic']['top3']:.0%}")
        print(f" 耗时: 建库 {ingest_s:.1f}s + 检索 {search_s:.1f}s")

        misses = [r for r in ranked_rows if not r["passed"]]
        if misses:
            print(f"\n 未通过用例（{len(misses)} 条）:")
            for r in misses:
                print(f"  [未命中]    {r['q']}  期望《{r['expect']}》 实际 top3: {r['filenames'][:3]}")
        top3_misses = [r for r in ranked_rows if r["passed"] and (r["best_rank"] or 99) > 3]
        if top3_misses:
            print(f"\n top3 未中但在引用池（观察项，不计失败）:")
            for r in top3_misses:
                print(f"  [rank={r['best_rank']}] {r['q']}")

        verdict = "PASS" if result["gate_passed"] else "FAIL"
        print(f"\n 回归门槛 top3 >= {args.gate:.0%}: {verdict}")

    return 0 if result["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
