"""
Auto-retry pra criar a VM ARM grátis (VM.Standard.A1.Flex) na Oracle Cloud.
Fica tentando até a Oracle ter capacidade. Avisa no Telegram quando conseguir.

Config OCI lido de ~/.oci/config (a private key .pem fica fora do projeto/OneDrive).
Descobre sozinho: subnet pública do VCN, imagem Ubuntu ARM mais recente.
Gera um par SSH novo (em ~/.oci/canal_dark_ssh) pra acessar a máquina depois.

Uso:
    python oci_retry_launch.py --check     # só testa config/descoberta e sai
    python oci_retry_launch.py             # loop infinito até criar a VM
"""
import sys
import time
from pathlib import Path

import oci
from oci.core import ComputeClient, VirtualNetworkClient
from oci.identity import IdentityClient
from oci.core.models import (
    LaunchInstanceDetails, LaunchInstanceShapeConfigDetails,
    InstanceSourceViaImageDetails, CreateVnicDetails,
)

VCN_NAME = "canal-dark-vcn"
DISPLAY_NAME = "canal-dark"
OCPUS = 1
MEM_GB = 6
RETRY_SECONDS = 60

OCI_DIR = Path.home() / ".oci"
SSH_PRIV = OCI_DIR / "canal_dark_ssh"
SSH_PUB = OCI_DIR / "canal_dark_ssh.pub"
VM_INFO = OCI_DIR / "canal_dark_vm.txt"
ENV_PATH = r"C:\Users\aless\canal-dark\.env"


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def telegram(msg):
    try:
        import os
        from dotenv import load_dotenv
        import requests
        load_dotenv(ENV_PATH)
        t = os.environ.get("TELEGRAM_BOT_TOKEN")
        c = os.environ.get("TELEGRAM_CHAT_ID")
        if t and c:
            requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
                          data={"chat_id": c, "text": msg}, timeout=20)
    except Exception as e:
        log(f"(telegram falhou: {e})")


def ensure_ssh_key():
    if SSH_PUB.exists():
        return SSH_PUB.read_text().strip()
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                         serialization.PublicFormat.OpenSSH)
    SSH_PRIV.write_bytes(priv)
    SSH_PUB.write_bytes(pub + b"\n")
    log(f"Par SSH novo gerado: {SSH_PRIV}")
    return pub.decode().strip()


def discover(config):
    identity = IdentityClient(config)
    compute = ComputeClient(config)
    net = VirtualNetworkClient(config)
    comp = config["tenancy"]  # compartimento raiz (conta nova)

    ads = [a.name for a in identity.list_availability_domains(compartment_id=comp).data]
    log(f"Autenticado OK. Availability Domains: {ads}")

    vcns = net.list_vcns(compartment_id=comp).data
    vcn = next((v for v in vcns if v.display_name == VCN_NAME), None)
    if not vcn:
        raise RuntimeError(f"VCN '{VCN_NAME}' nao encontrada. Existentes: {[v.display_name for v in vcns]}")
    subnets = net.list_subnets(compartment_id=comp, vcn_id=vcn.id).data
    pub = next((s for s in subnets if not s.prohibit_public_ip_on_vnic), None)
    if not pub:
        raise RuntimeError("Nenhuma subnet PUBLICA no VCN.")
    log(f"Subnet publica: {pub.display_name}")

    imgs = compute.list_images(compartment_id=comp, operating_system="Canonical Ubuntu",
                               shape="VM.Standard.A1.Flex", sort_by="TIMECREATED",
                               sort_order="DESC").data
    if not imgs:
        raise RuntimeError("Nenhuma imagem Ubuntu ARM (A1) encontrada.")
    log(f"Imagem ARM: {imgs[0].display_name}")

    return compute, net, comp, ads, pub.id, imgs[0].id


def main():
    check_only = "--check" in sys.argv
    config = oci.config.from_file()  # ~/.oci/config
    oci.config.validate_config(config)

    compute, net, comp, ad_names, subnet_id, image_id = discover(config)
    ssh_pub = ensure_ssh_key()

    if check_only:
        log("CHECK OK — config, subnet, imagem e SSH prontos. (sem lancar)")
        return

    base = dict(
        compartment_id=comp, shape="VM.Standard.A1.Flex",
        shape_config=LaunchInstanceShapeConfigDetails(ocpus=OCPUS, memory_in_gbs=MEM_GB),
        source_details=InstanceSourceViaImageDetails(image_id=image_id),
        display_name=DISPLAY_NAME,
        metadata={"ssh_authorized_keys": ssh_pub},
    )

    once = "--once" in sys.argv
    if not once:
        telegram(f"Auto-retry ligado: tentando criar a VM ARM gratis a cada {RETRY_SECONDS}s. Te aviso quando pegar vaga.")
    log("Tentativa unica..." if once else "Iniciando loop de retry...")
    attempt = 0
    while True:
        attempt += 1
        for ad in ad_names:
            try:
                details = LaunchInstanceDetails(
                    availability_domain=ad,
                    create_vnic_details=CreateVnicDetails(subnet_id=subnet_id, assign_public_ip=True),
                    **base,
                )
                inst = compute.launch_instance(details).data
                log(f"VM CRIADA! id={inst.id} (AD={ad}). Aguardando RUNNING...")
                while True:
                    st = compute.get_instance(inst.id).data.lifecycle_state
                    if st == "RUNNING":
                        break
                    if st in ("TERMINATED", "TERMINATING", "FAILED"):
                        log(f"Estado inesperado: {st}")
                        break
                    time.sleep(8)
                ip = None
                va = compute.list_vnic_attachments(compartment_id=comp, instance_id=inst.id).data
                if va:
                    ip = net.get_vnic(va[0].vnic_id).data.public_ip
                log(f"PRONTO! IP publico: {ip}")
                VM_INFO.write_text(f"id={inst.id}\nip={ip}\nad={ad}\nssh_key={SSH_PRIV}\n")
                telegram(f"VM ARM criada! IP: {ip}\nProximo: instalar o pipeline (avise o Claude).")
                return
            except oci.exceptions.ServiceError as e:
                msg = (e.message or "").lower()
                if e.status == 500 and ("capacity" in msg or e.code in ("InternalError", "OutOfHostCapacity", "OutOfCapacity")):
                    log(f"[tentativa {attempt}] AD {ad}: sem capacidade.")
                    continue
                if e.status == 429:
                    log(f"[tentativa {attempt}] rate limit (429). aguardando...")
                    time.sleep(RETRY_SECONDS)
                    continue
                log(f"ERRO FATAL OCI: status={e.status} code={e.code} msg={e.message}")
                telegram(f"Auto-retry parou (erro): {e.code} - {e.message}")
                return
            except Exception as e:
                log(f"erro inesperado: {e}")
        if once:
            log("Sem capacidade nesta tentativa. (--once)")
            return
        time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
