"""Render saved report artifacts with the current template, without model calls."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neurogra.clinical.reporting import render_saved_report


def main():
    parser = argparse.ArgumentParser(description='离线按定稿模板重排已有报告；不调用模型，不覆盖原报告。')
    parser.add_argument('--source', required=True, type=Path, help='含 report.json 和 provenance.json 的目录')
    parser.add_argument('--output', required=True, type=Path, help='新的输出目录')
    parser.add_argument('--preparation', type=Path, help='历史报告需要对应的 preparation.json，以保留影像资产及资料清单')
    args = parser.parse_args()
    print(render_saved_report(args.source, args.output, args.preparation))


if __name__ == '__main__':
    main()
