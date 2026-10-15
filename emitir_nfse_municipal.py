#!/usr/bin/env python3
"""Explicit São Paulo municipal schema-v2 test/production entry point."""
import argparse
import json
import sys
from fiscal_safety import dispatch_emission, fiscal_status, FiscalOperationBlocked


def emitir_nota(config_file, dados_file, modo, layout=2, *, dry_run=False):
    return dispatch_emission(config_file, dados_file, modo, layout, dry_run=dry_run)


def _emitir_layout2(config_file, dados_file, modo, *, dry_run=False):
    return emitir_nota(config_file, dados_file, modo, 2, dry_run=dry_run)


def construir_dps_xml(*args, **kwargs):
    raise FiscalOperationBlocked('DPS nacional não é o contrato municipal desta skill.')


def enviar_dps_nacional(*args, **kwargs):
    raise FiscalOperationBlocked('Transporte nacional indisponível nesta skill municipal.')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='NFS-e SP: prévia local, teste municipal e produção explícita no layout 2')
    parser.add_argument('--modo', choices=['teste', 'producao'], default='teste')
    parser.add_argument('--layout', type=int, choices=[1, 2], default=2)
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--dados')
    parser.add_argument('--status', action='store_true', help='Consulta local da produção e numeração; sem rede/certificado/emissão')
    parser.add_argument('--json-out', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Sem rede, certificado, assinatura ou alteração do contador, em ambos os layouts')
    args = parser.parse_args()
    if not args.status and not args.dados:
        parser.error('Informar --dados explicitamente, ou usar --status.')
    result = fiscal_status(args.config) if args.status else emitir_nota(args.config, args.dados, args.modo, args.layout, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get('sucesso') else 2)
