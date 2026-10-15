"""Local municipal preview CLI: no certificate, network or RPS reservation."""
import argparse
import json
import sys
from fiscal_safety import preview_emission

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--dados', required=True)
    args = parser.parse_args()
    result = preview_emission(args.config, args.dados, 'teste', 2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['sucesso'] else 2)
