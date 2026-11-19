#!/usr/bin/env bash
# Изолированный TLS-вход контроллера.
set -euo pipefail
umask 077

usage() {
  echo 'Укажите абсолютный путь: exam-controller-tls.sh КАТАЛОГ [--server 10.135.250.231 [--renew]]' >&2
  exit 64
}

if (($# < 1)) || [[ "$1" != /* ]]; then
  usage
fi
if [[ ! -d "$1" || ! -O "$1" || -L "$1" || $(stat -c %a -- "$1") != 700 ]]; then
  echo 'Каталог контроллера должен принадлежать оператору и иметь права 0700.' >&2
  exit 64
fi

state="$1"
shift
listen_ip=127.0.0.1
certificate_ip=10.0.2.55
acl=''
renew=false
if (($#)); then
  if (($# < 2 || $# > 3)) || [[ "$1" != --server || "$2" != 10.135.250.231 ]]; then
    usage
  fi
  listen_ip="$2"
  certificate_ip="$2"
  acl='allow 10.0.0.0/8; deny all;'
  if (($# == 3)); then
    [[ "$3" = --renew ]] || usage
    renew=true
  fi
fi

repository=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec 9>"$state/tls.lock"
flock 9

protected_file() {
  [[ -f "$1" && -s "$1" && -O "$1" && ! -L "$1" && $(stat -c %a -- "$1") = 600 ]]
}

teacher_route=''
teacher_auth="$state/teacher.htpasswd"
if [[ -n "$acl" && (-e "$teacher_auth" || -L "$teacher_auth") ]]; then
  if ! protected_file "$teacher_auth" || ! protected_file "$state/teacher-proxy-token"; then
    echo 'Файлы teacher.htpasswd и teacher-proxy-token должны принадлежать оператору, быть непустыми и иметь права 0600.' >&2
    exit 1
  fi
  token_size=$(stat -c %s -- "$state/teacher-proxy-token")
  teacher_token=$(<"$state/teacher-proxy-token")
  if [[ ("$token_size" != 64 && "$token_size" != 65) || ! "$teacher_token" =~ ^[a-f0-9]{64}$ ]] ||
    ! LC_ALL=C grep -aEqx '[a-f0-9]{64}' "$state/teacher-proxy-token"; then
    echo 'teacher-proxy-token должен содержать 64 шестнадцатеричных символа в нижнем регистре.' >&2
    exit 1
  fi
  teacher_route='include teacher-route.conf;'
fi

if [[ -e "$state/server-ip" ]]; then
  if [[ $(<"$state/server-ip") != "$listen_ip" ]]; then
    echo 'Каталог контроллера закреплён за другим адресом сервера.' >&2
    exit 1
  fi
elif [[ -n "$acl" ]]; then
  if [[ -e "$state/tls" || "$renew" = true ]]; then
    echo 'Для первой серверной настройки нужен отдельный каталог без TLS.' >&2
    exit 1
  fi
  printf '%s\n' "$listen_ip" >"$state/server-ip.tmp"
  mv -T -- "$state/server-ip.tmp" "$state/server-ip"
fi

issue_leaf() {
  local output="$1" issuer="$2" private_key="$3"
  openssl req -new -key "$private_key" -subj '/CN=Exam lab controller' \
    -out "$output/server.csr" 2>>"$output/openssl.log"
  cat >"$output/server.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=IP:$certificate_ip
EOF
  openssl x509 -req -in "$output/server.csr" -CA "$issuer/ca.crt" -CAkey "$issuer/ca.key" \
    -days 180 -sha256 -extfile "$output/server.ext" -out "$output/server.crt" 2>>"$output/openssl.log"
  openssl verify -purpose sslserver -verify_ip "$certificate_ip" \
    -CAfile "$issuer/ca.crt" "$output/server.crt" >/dev/null
  rm -- "$output/server.csr" "$output/server.ext" "$output/openssl.log"
}

verify_keys() {
  local directory="$1" name certificate_key private_key
  for name in ca server; do
    certificate_key=$(openssl x509 -in "$directory/$name.crt" -noout -pubkey)
    private_key=$(openssl pkey -in "$directory/$name.key" -pubout)
    if [[ "$certificate_key" != "$private_key" ]]; then
      echo 'Ключ контроллера не соответствует сохранённому сертификату.' >&2
      exit 1
    fi
  done
}

if [[ ! -e "$state/tls" || "$renew" = true ]]; then
  temporary=$(mktemp -d -- "$state/.tls.XXXXXX")
  trap 'rm -rf -- "$temporary"' EXIT
  if [[ "$renew" = true ]]; then
    verify_keys "$state/tls"
    openssl verify -no_check_time -purpose sslserver -verify_ip "$certificate_ip" \
      -CAfile "$state/tls/ca.crt" "$state/tls/server.crt" >/dev/null
    issue_leaf "$temporary" "$state/tls" "$state/tls/server.key"
    mv -T -- "$temporary/server.crt" "$state/tls/server.crt"
    rmdir -- "$temporary"
  else
    openssl req -x509 -newkey rsa:3072 -sha256 -noenc -days 365 -subj '/CN=Exam lab controller CA' \
      -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
      -addext 'keyUsage=critical,keyCertSign,cRLSign' \
      -keyout "$temporary/ca.key" -out "$temporary/ca.crt" 2>"$temporary/openssl.log"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
      -out "$temporary/server.key" 2>>"$temporary/openssl.log"
    issue_leaf "$temporary" "$temporary" "$temporary/server.key"
    verify_keys "$temporary"
    mv -T -- "$temporary" "$state/tls"
  fi
  trap - EXIT
fi

verify_keys "$state/tls"
openssl verify -purpose sslserver -verify_ip "$certificate_ip" \
  -CAfile "$state/tls/ca.crt" "$state/tls/server.crt" >/dev/null

openssl x509 -in "$state/tls/ca.crt" -outform DER -out "$state/controller-ca.cer.tmp"
mv -T -- "$state/controller-ca.cer.tmp" "$state/controller-ca.cer"
if [[ -n "$teacher_route" ]]; then
  install -m 600 -- "$repository/deploy/exam-controller-teacher.nginx.conf" "$state/teacher-route.conf.tmp"
  mv -T -- "$state/teacher-route.conf.tmp" "$state/teacher-route.conf"
  printf 'proxy_set_header X-Exam-Teacher-Proxy "%s";\n' "$teacher_token" >"$state/teacher-proxy-token.conf.tmp"
  mv -T -- "$state/teacher-proxy-token.conf.tmp" "$state/teacher-proxy-token.conf"
fi
sed -e "s|CONTROLLER_BIND_IPV4|$listen_ip|g" -e "s|CONTROLLER_CLIENT_ACL|$acl|g" \
  -e "s|CONTROLLER_TEACHER_ROUTE|$teacher_route|g" \
  "$repository/deploy/exam-controller.nginx.conf" >"$state/nginx.conf.tmp"
mv -T -- "$state/nginx.conf.tmp" "$state/nginx.conf"
sha256sum -- "$state/controller-ca.cer"
