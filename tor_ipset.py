import requests
import boto3
import json
from ipaddress import ip_address


# Configurações
IPSET_NAME = "TOR-IPSet"            # Nome do IPSet criado no WAF
IPSET_SCOPE = "CLOUDFRONT"                       # Pode ser "REGIONAL" ou "CLOUDFRONT"
AWS_REGION = "us-east-1"                       # Região AWS onde o IPSet está

# Inicializa o cliente boto3 para o WAFv2
waf = boto3.client("wafv2", region_name=AWS_REGION)

# Função para buscar a lista de exit nodes da TorBulkExitList
# Esta lista contém apenas IPs, um por linha

def fetch_torbulkexitlist():
    resp = requests.get("https://check.torproject.org/torbulkexitlist")
    resp.raise_for_status()
    return set(line.strip() for line in resp.text.splitlines() if line.strip())

# Função para buscar IPs de exit nodes através da API Onionoo
# Retorna um conjunto de IPs listados no campo 'exit_addresses'

def fetch_onionoo_exit_addresses():
    exit_ips = set()
    url = "https://onionoo.torproject.org/details?flag=Exit&running=true"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    for relay in data.get("relays", []):
        for ip in relay.get("exit_addresses", []):
            try:
                exit_ips.add(str(ip_address(ip)))
            except ValueError:
                continue  # Ignora IPs inválidos
    return exit_ips

# Função que localiza e retorna um IPSet existente pelo nome e escopo

def get_ipset():
    next_marker = None
    while True:
        if next_marker:
            response = waf.list_ip_sets(Scope=IPSET_SCOPE, NextMarker=next_marker)
        else:
            response = waf.list_ip_sets(Scope=IPSET_SCOPE)
        for ipset_summary in response["IPSets"]:
            if ipset_summary["Name"] == IPSET_NAME:
                ipset_response = waf.get_ip_set(
                    Name=IPSET_NAME,
                    Scope=IPSET_SCOPE,
                    Id=ipset_summary["Id"]
                )
                ipset = ipset_response["IPSet"]
                ipset["LockToken"] = ipset_response["LockToken"]  # Adiciona o LockToken ao dict
                return ipset
        if "NextMarker" in response:
            next_marker = response["NextMarker"]
        else:
            break
    raise Exception(f"IPSet '{IPSET_NAME}' não encontrado.")

# Função para atualizar o IPSet no AWS WAF
# Substitui o conjunto atual de IPs pelo novo conjunto combinado

def update_ipset(new_ips):
    ipset = get_ipset()
    current_ips = set(ipset["Addresses"])
    desired_ips = set(f"{ip}/32" for ip in new_ips)  # Formato exigido pelo WAF

    if current_ips == desired_ips:
        print("Nenhuma mudança detectada no IPSet.")
        return

    response = waf.update_ip_set(
        Name=ipset["Name"],
        Scope=IPSET_SCOPE,
        Id=ipset["Id"],
        LockToken=ipset["LockToken"],
        Addresses=sorted(list(desired_ips))
    )
    print("IPSet atualizado com sucesso.")

# Função principal: coleta as listas, deduplica, compara e atualiza

def main():
    try:
        print("Obtendo listas de IPs da rede Tor...")
        torbulk_ips = fetch_torbulkexitlist()
        onionoo_ips = fetch_onionoo_exit_addresses()

        combined_ips = torbulk_ips.union(onionoo_ips)
        print(f"Total de IPs combinados: {len(combined_ips)}")
        print("Exemplo de IPs combinados:")
        for ip in list(sorted(combined_ips))[:20]:  # Mostra os 20 primeiros para não poluir o terminal
            print(ip)
        # print(sorted(combined_ips))  # Descomente se quiser ver todos

        update_ipset(combined_ips)  # Agora a atualização será feita de verdade!
    except Exception as e:
        print(f"Erro durante execução: {e}")

if __name__ == "__main__":
    main()
