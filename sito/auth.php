<?php
/**
 * Protezione con password di Dungeons & Byte, a GRUPPI (un utente per gruppo) con registro degli accessi.
 *
 * Configurazione: /var/www/dnb_config.php (FUORI dalla web root, gestita col comando `dnb-auth`).
 *   auth_enabled     false -> sito aperto a tutti
 *   users            gruppi: nome => [hash bcrypt, attivo, creato]
 *   secret           chiave per firmare il cookie di accesso
 *   public_projects  volumi consultabili anche senza accesso quando la protezione e' attiva
 *   session_days     durata dell'accesso dopo il login
 *   log_days         giorni di conservazione del registro accessi (default 30)
 *   trusted_proxies  indirizzi/reti del proxy (NPM): solo da li' si legge l'IP reale del visitatore
 *
 * Cookie: "gruppo(b64).scadenza.firma". La firma include l'hash della password del gruppo: cambiare la password
 * o togliere/disattivare il gruppo scollega subito i suoi dispositivi; ruotare il secret li scollega tutti.
 * Registro: DNB_DATA_DIR/accessi.jsonl (una riga JSON per evento), ripulito dopo log_days giorni da `dnb-auth log purge`.
 */

const DNB_CONFIG_FILE = '/var/www/dnb_config.php';
const DNB_DATA_DIR    = '/var/www/dnb_data';
const DNB_COOKIE      = 'dnb_auth';
const DNB_DUMMY_HASH  = '$2y$11$kJTdfbZpAWCTw.9hggQ/pO3j.nXli4XzP/JuHmey32jSgA63Mcgmu';   // per non distinguere "gruppo inesistente" da "password errata" dal tempo

function dnb_config(): array {
    static $cfg = null;
    if ($cfg === null) {
        $cfg = is_file(DNB_CONFIG_FILE) ? (include DNB_CONFIG_FILE) : [];
        $cfg = (is_array($cfg) ? $cfg : []) + [
            'auth_enabled' => false, 'password_hash' => '', 'secret' => '', 'users' => [],
            'public_projects' => [], 'session_days' => 30, 'log_days' => 30, 'trusted_proxies' => [],
        ];
        // compatibilita': la vecchia password unica diventa il gruppo "principale"
        if (!$cfg['users'] && $cfg['password_hash'] !== '') {
            $cfg['users'] = ['principale' => ['hash' => $cfg['password_hash'], 'attivo' => true, 'creato' => '']];
        }
        // Senza almeno un gruppo attivo (o senza secret) non si accende mai la protezione: meglio aperto che bloccati fuori.
        $attivi = array_filter($cfg['users'], fn($u) => !empty($u['attivo']) && !empty($u['hash']));
        if (!$attivi || $cfg['secret'] === '') $cfg['auth_enabled'] = false;
    }
    return $cfg;
}

function dnb_users(): array {
    return array_filter(dnb_config()['users'], fn($u) => !empty($u['attivo']) && !empty($u['hash']));
}

function dnb_auth_enabled(): bool { return (bool)dnb_config()['auth_enabled']; }

function dnb_is_https(): bool {
    return (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https') || !empty($_SERVER['HTTPS']);
}

function dnb_b64u(string $s): string { return rtrim(strtr(base64_encode($s), '+/', '-_'), '='); }
function dnb_b64u_dec(string $s): string { return (string)base64_decode(strtr($s, '-_', '+/')); }

function dnb_sign(string $user, int $exp, string $hash): string {
    return hash_hmac('sha256', $user . '|' . $exp, dnb_config()['secret'] . $hash);
}

/** Nome del gruppo autenticato (stringa vuota se la protezione e' spenta), null se non autenticato. */
function dnb_session(): ?string {
    if (!dnb_auth_enabled()) return '';
    $tok = $_COOKIE[DNB_COOKIE] ?? '';
    if (!preg_match('/^([A-Za-z0-9_-]{1,100})\.(\d{9,11})\.([a-f0-9]{64})$/', $tok, $m)) return null;
    $user = dnb_b64u_dec($m[1]);
    $u = dnb_users()[$user] ?? null;
    if (!$u || (int)$m[2] <= time()) return null;
    return hash_equals(dnb_sign($user, (int)$m[2], $u['hash']), $m[3]) ? $user : null;
}

function dnb_authenticated(): bool { return !dnb_auth_enabled() || dnb_session() !== null; }

function dnb_project_allowed(string $project): bool {
    return dnb_authenticated() || in_array($project, dnb_config()['public_projects'], true);
}

/* ───────── IP reale del visitatore ─────────
 * Apache vede solo il proxy (NPM). L'IP vero si legge dalle intestazioni SOLO se la connessione arriva da un proxy fidato:
 * X-Real-IP e' scritto da NPM (l'indirizzo che NPM ha visto); se quello e' un server Cloudflare, il visitatore e' in CF-Connecting-IP.
 * Altrimenti le intestazioni si ignorano (nessuno puo' falsificare l'IP nel registro). */
function dnb_in_cidr(string $ip, string $cidr): bool {
    [$net, $bits] = array_pad(explode('/', $cidr, 2), 2, null);
    $a = @inet_pton($ip); $b = @inet_pton($net);
    if ($a === false || $b === false || strlen($a) !== strlen($b)) return false;
    $bits = $bits === null ? strlen($a) * 8 : (int)$bits;
    $bytes = intdiv($bits, 8);
    if ($bytes && substr($a, 0, $bytes) !== substr($b, 0, $bytes)) return false;
    $rem = $bits % 8;
    if ($rem) { $mask = (0xFF << (8 - $rem)) & 0xFF; if ((ord($a[$bytes]) & $mask) !== (ord($b[$bytes]) & $mask)) return false; }
    return true;
}

function dnb_is_cloudflare(string $ip): bool {
    static $ranges = null;
    if ($ranges === null) {
        $f = DNB_DATA_DIR . '/cloudflare_ips.txt';
        $ranges = is_file($f) ? array_filter(array_map('trim', file($f))) : [];
    }
    foreach ($ranges as $r) if (dnb_in_cidr($ip, $r)) return true;
    return false;
}

function dnb_client_ip(): string {
    $peer = $_SERVER['REMOTE_ADDR'] ?? '?';
    if ($peer === '?') return '?';
    $fidati = dnb_config()['trusted_proxies'];
    foreach ($fidati as $t) {
        if (!dnb_in_cidr($peer, $t)) continue;
        $xr = trim($_SERVER['HTTP_X_REAL_IP'] ?? '');
        if (!filter_var($xr, FILTER_VALIDATE_IP)) return $peer;
        $cf = trim($_SERVER['HTTP_CF_CONNECTING_IP'] ?? '');
        return (filter_var($cf, FILTER_VALIDATE_IP) && dnb_is_cloudflare($xr)) ? $cf : $xr;
    }
    return $peer;
}

function dnb_client_key(): string { return substr(hash('sha256', dnb_client_ip()), 0, 16); }

/* ───────── Registro accessi ───────── */
function dnb_state(?callable $mod = null): array {
    $f = DNB_DATA_DIR . '/stato.json';
    if (!is_dir(DNB_DATA_DIR)) return [];
    $db = is_file($f) ? (json_decode((string)file_get_contents($f), true) ?: []) : [];
    if ($mod) { $db = $mod($db); file_put_contents($f, json_encode($db), LOCK_EX); }
    return $db;
}

/** true al massimo una volta ogni $secs secondi per la stessa chiave (evita registri enormi). */
function dnb_once(string $key, int $secs): bool {
    $ok = false; $now = time();
    dnb_state(function ($db) use ($key, $secs, $now, &$ok) {
        foreach ($db as $k => $t) if ($t < $now - 86400) unset($db[$k]);
        if (($db[$key] ?? 0) < $now - $secs) { $db[$key] = $now; $ok = true; }
        return $db;
    });
    return $ok;
}

function dnb_log(string $evento, string $utente = '', string $dettaglio = ''): void {
    if (!is_dir(DNB_DATA_DIR)) return;
    $ora = (new DateTime('now', new DateTimeZone('Europe/Rome')))->format('c');
    $r = ['t' => $ora, 'e' => $evento, 'u' => $utente, 'ip' => dnb_client_ip(),
          'ua' => substr(preg_replace('/[\x00-\x1f]/', '', $_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 140)];
    if ($dettaglio !== '') $r['d'] = $dettaglio;
    @file_put_contents(DNB_DATA_DIR . '/accessi.jsonl', json_encode($r, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND | LOCK_EX);
}

/** Anti brute-force: max 8 tentativi falliti ogni 10 minuti per IP, 60 in totale. */
function dnb_throttle(string $key, ?bool $failed = null): bool {
    $file = sys_get_temp_dir() . '/dnb_auth_attempts.json';
    $now  = time();
    $db   = is_file($file) ? (json_decode((string)file_get_contents($file), true) ?: []) : [];
    foreach ($db as $k => $list) {
        $db[$k] = array_values(array_filter($list, fn($t) => $t > $now - 600));
        if (!$db[$k]) unset($db[$k]);
    }
    if ($failed === true) { $db[$key][] = $now; $db['*'][] = $now; file_put_contents($file, json_encode($db), LOCK_EX); }
    if ($failed === false) { unset($db[$key]); file_put_contents($file, json_encode($db), LOCK_EX); }
    return count($db[$key] ?? []) < 8 && count($db['*'] ?? []) < 60;
}

/** Gestisce auth/login/logout; per le altre azioni i controlli stanno nei singoli endpoint. */
function dnb_gate(): void {
    $action = $_GET['action'] ?? '';
    if (!in_array($action, ['auth', 'login', 'logout'], true)) return;
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    $cfg = dnb_config();

    if ($action === 'auth') {
        $u = dnb_session();
        if ($u !== null && $u !== '' && dnb_once('visita:' . $u . '|' . dnb_client_key(), 6 * 3600)) dnb_log('visita', $u);
        echo json_encode(['enabled' => dnb_auth_enabled(), 'authenticated' => $u !== null, 'user' => $u ?: null,
                          'public_projects' => dnb_auth_enabled() ? count($cfg['public_projects']) : null]);
        exit;
    }
    if ($action === 'logout') {
        $u = dnb_session();
        if ($u) dnb_log('logout', $u);
        setcookie(DNB_COOKIE, '', ['expires' => 1, 'path' => '/', 'secure' => dnb_is_https(), 'httponly' => true, 'samesite' => 'Lax']);
        echo json_encode(['ok' => true]);
        exit;
    }
    // login
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') { http_response_code(405); echo json_encode(['error' => 'POST richiesto']); exit; }
    if (!dnb_auth_enabled()) { echo json_encode(['ok' => true, 'enabled' => false]); exit; }
    $key = dnb_client_key();
    if (!dnb_throttle($key)) {
        if (dnb_once('blocco:' . $key, 300)) dnb_log('bloccato', '', 'troppi tentativi');
        http_response_code(429); echo json_encode(['error' => 'Troppi tentativi. Riprova tra qualche minuto.']); exit;
    }
    $body = json_decode((string)file_get_contents('php://input'), true);
    $pw   = is_array($body) ? (string)($body['password'] ?? '') : '';
    $nome = is_array($body) ? strtolower(trim((string)($body['user'] ?? ''))) : '';
    $utenti = dnb_users();
    if ($nome === '' && count($utenti) === 1) $nome = (string)array_key_first($utenti);   // un solo gruppo: non serve indicarlo
    $u = $utenti[$nome] ?? null;
    $ok = password_verify($pw, $u['hash'] ?? DNB_DUMMY_HASH) && $u !== null && $pw !== '';
    if (!$ok) {
        dnb_throttle($key, true);
        // il nome tentato si registra solo se e' un gruppo vero (altrimenti potrebbe essere una password scritta nel campo sbagliato)
        dnb_log('login_fallito', $u !== null ? $nome : '(sconosciuto)');
        usleep(900000);
        http_response_code(401);
        echo json_encode(['error' => 'Gruppo o password errati']);
        exit;
    }
    dnb_throttle($key, false);
    $exp = time() + 86400 * max(1, (int)$cfg['session_days']);
    setcookie(DNB_COOKIE, dnb_b64u($nome) . '.' . $exp . '.' . dnb_sign($nome, $exp, $u['hash']),
              ['expires' => $exp, 'path' => '/', 'secure' => dnb_is_https(), 'httponly' => true, 'samesite' => 'Lax']);
    dnb_log('login', $nome);
    echo json_encode(['ok' => true, 'user' => $nome]);
    exit;
}
