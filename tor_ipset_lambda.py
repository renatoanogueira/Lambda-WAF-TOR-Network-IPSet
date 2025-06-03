import requests
import boto3
import json
import os
from ipaddress import ip_address

# Configurações principais do IPSet e AWS
IPSET_NAME = "Tor-IPSet"
IPSET_SCOPE = "CLOUDFRONT"  # ou "REGIONAL" se não for CloudFront
AWS_REGION = "us-east-1"    # Região obrigatória para CLOUDFRONT

# Inicializa clientes boto3 para WAFv2 e SNS
waf = boto3.client("wafv2", region_name=AWS_REGION)
sns = boto3.client("sns")

# Lê o ARN do tópico SNS da variável de ambiente
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

# Busca a lista de IPs de exit nodes do TorBulkExitList
def fetch_torbulkexitlist():
    resp = requests.get("https://check.torproject.org/torbulkexitlist")
    resp.raise_for_status()  # Lança exceção se a requisição falhar
    # Retorna um set de IPs, um por linha
    return set(line.strip() for line in resp.text.splitlines() if line.strip())

# Busca IPs de exit nodes via API Onionoo
def fetch_onionoo_exit_addresses():
    exit_ips = set()
    url = "https://onionoo.torproject.org/details?flag=Exit&running=true"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    for relay in data.get("relays", []):
        for ip in relay.get("exit_addresses", []):
            try:
                exit_ips.add(str(ip_address(ip)))  # Valida e adiciona IP
            except ValueError:
                continue  # Ignora IPs inválidos
    return exit_ips

# Localiza e retorna o IPSet existente pelo nome e escopo, incluindo o LockToken
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
                ipset["LockToken"] = ipset_response["LockToken"]
                return ipset
        if "NextMarker" in response:
            next_marker = response["NextMarker"]
        else:
            break
    raise Exception(f"🚨 WAF - IPSet '{IPSET_NAME}' não encontrado.")

# Atualiza o IPSet no AWS WAF, substituindo os IPs atuais pelo novo conjunto
def update_ipset(new_ips):
    ipset = get_ipset()
    current_ips = set(ipset["Addresses"])
    desired_ips = set(f"{ip}/32" for ip in new_ips)  # Formato exigido pelo WAF

    if current_ips == desired_ips:
        print("Nenhuma mudança detectada no IPSet.")
        return False, len(desired_ips)  # Não houve atualização, mas é sucesso

    # Atualiza o IPSet na AWS
    waf.update_ip_set(
        Name=ipset["Name"],
        Scope=IPSET_SCOPE,
        Id=ipset["Id"],
        LockToken=ipset["LockToken"],
        Addresses=sorted(list(desired_ips))
    )
    print("IPSet atualizado com sucesso.")
    return True, len(desired_ips)  # Atualização feita

# Função principal do Lambda
def lambda_handler(event, context):
    result_message = ""
    try:
        print("Obtendo listas de IPs da rede Tor...")
        torbulk_ips = fetch_torbulkexitlist()           # Busca lista TorBulk
        onionoo_ips = fetch_onionoo_exit_addresses()    # Busca lista Onionoo
        combined_ips = torbulk_ips.union(onionoo_ips)   # Junta e deduplica IPs
        print(f"Total de IPs combinados: {len(combined_ips)}")

        updated, ip_count = update_ipset(combined_ips)  # Atualiza IPSet
        if updated:
            result_message = f"✅ O IPSet '{IPSET_NAME}' foi atualizado e tem agora {ip_count} IPs."
        else:
            result_message = f"👍 O IPSet '{IPSET_NAME}' não precisou de atualização. Continua com ({ip_count} IPs)."
    except Exception as e:
        result_message = f"Erro durante execução: {e}"
        if SNS_TOPIC_ARN:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="❌ Erro na execução do Lambda WAF - IPSet TOR Network Update",
                Message=result_message
            )
        print(result_message)
        raise  # Garante que o erro seja registrado como falha no Lambda
    else:
        # Só notifica sucesso se não houve erro
        if SNS_TOPIC_ARN:
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="🔔 Resultado da execução do Lambda WAF - IPSet TOR Network Update",
                Message=result_message
            )
        print(result_message)
