*** Settings ***
Library    SSHLibrary
Library    String
Test Setup    Stop after a failed step

*** Variables ***
${IMAGE_URL}         ghcr.io/platypuschan/matrix-reworked:latest
# The update scenario starts from the last published release of this module;
# test-module-update.sh looks it up in the registry.
${PREVIOUS_IMAGE_URL}    ${EMPTY}
${SCENARIO}          install
${HOST}              matrix.dom.test
${SERVER_NAME}       dom.test
${ELEMENT_HOST}      chat.dom.test
${CINNY_HOST}        cinny.dom.test
${KETESA_HOST}       admin.dom.test
${USER_DOMAIN}       ldap.dom.test
${USER_PASSWORD}     Nethesis,1234
${MESSAGE}           kept-across-the-update
${module_id}         ${EMPTY}
${room_id}           ${EMPTY}
# Not ${admin_password}: Robot variable names ignore case, which would
# overwrite ${ADMIN_PASSWORD} of the cluster-admin login below.
${matrix_admin_password}    ${EMPTY}
${admin_token}       ${EMPTY}
${ADMIN_USER}        admin
${ADMIN_PASSWORD}    Nethesis,1234

*** Keywords ***
Stop after a failed step
    # A hanging step must not cascade through every later timeout
    Skip If    '${PREV TEST STATUS}' == 'FAIL'    a previous step failed

Login to cluster-admin
    New Page    https://${NODE_ADDR}/cluster-admin/
    Fill Text    text="Username"    ${ADMIN_USER}
    Click    button >> text="Continue"
    Fill Text    text="Password"    ${ADMIN_PASSWORD}
    Click    button >> text="Log in"
    Wait For Elements State    css=#main-content    visible    timeout=10s

Run cluster task
    [Arguments]    ${path}    ${payload}
    ${output}    ${rc} =    Execute Command    api-cli run ${path} --data '${payload}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    ${path} failed: ${output}
    ${result} =    Evaluate    json.loads(r'''${output}''') if r'''${output}'''.strip() else {}    modules=json
    RETURN    ${result}

Run action
    [Arguments]    ${action}    ${payload}
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/${action} --data '${payload}'
    ...    return_rc=True
    RETURN    ${output}    ${rc}

Configure current module
    [Arguments]    &{extra}
    ${payload} =    Evaluate    json.dumps({"host": $HOST, "server_name": $SERVER_NAME, "http2https": False, "lets_encrypt": False, "user_domain": $USER_DOMAIN, "federation": True, "wellknown_on_server_name": True, "element_host": $ELEMENT_HOST, "cinny_host": $CINNY_HOST, "ketesa_host": $KETESA_HOST, "bridges": ["whatsapp", "discord"], "turn": True, "element_call": True, **$extra})    modules=json
    ${output}    ${rc} =    Run action    configure-module    ${payload}
    Should Be Equal As Integers    ${rc}    0    configure-module failed: ${output}

Get configuration
    ${output}    ${rc} =    Execute Command    api-cli run module/${module_id}/get-configuration
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    get-configuration failed: ${output}
    &{config} =    Evaluate    json.loads(r'''${output}''')    modules=json
    RETURN    &{config}

Matrix on
    [Arguments]    ${host}    @{args}    ${token}=${EMPTY}
    ${auth} =    Set Variable If    $token    --token ${token}    ${EMPTY}
    ${command} =    Catenate    python3 /root/matrix_client.py --host ${host} ${auth}    @{args}
    ${out}    ${err}    ${rc} =    Execute Command    ${command}    return_stderr=True    return_rc=True
    Should Be Equal As Integers    ${rc}    0    matrix_client ${args} failed: ${err}
    ${result} =    Evaluate    json.loads($out)    modules=json
    RETURN    ${result}

Matrix
    [Arguments]    @{args}    ${token}=${EMPTY}
    ${result} =    Matrix on    ${HOST}    @{args}    token=${token}
    RETURN    ${result}

Token of
    [Arguments]    ${user}    ${password}
    ${login} =    Matrix    login    ${user}    '${password}'
    RETURN    ${login}[access_token]

Read admin credentials
    &{config} =    Get configuration
    Should Be Equal    ${config.admin_user}    @matrixadmin:${SERVER_NAME}
    Should Not Be Empty    ${config.admin_password}
    Set Suite Variable    ${matrix_admin_password}    ${config.admin_password}
    # Synapse limits logins per account: log in once and keep the token
    IF    not $admin_token
        ${token} =    Token of    matrixadmin    ${matrix_admin_password}
        Set Suite Variable    ${admin_token}    ${token}
    END

Unit is
    [Arguments]    ${unit}    ${state}
    ${output} =    Execute Command    runagent -m ${module_id} systemctl --user is-active ${unit}
    Should Be Equal    ${output}    ${state}    ${unit} is ${output}

Container id
    [Arguments]    ${name}
    ${id} =    Execute Command    runagent -m ${module_id} podman inspect --format {{.Id}} ${name}
    RETURN    ${id}

Bot answers
    [Arguments]    ${bot}
    ${result} =    Matrix    bot-reply    @${bot}:${SERVER_NAME}    token=${admin_token}
    Should Not Be Empty    ${result}[reply]

Users chat through the LDAP domain
    ${token1} =    Token of    user1    ${USER_PASSWORD}
    ${room} =    Matrix    create-room    @user2:${SERVER_NAME}    ${MESSAGE}    token=${token1}
    Set Suite Variable    ${room_id}    ${room}[room_id]
    user2 reads the message

user2 reads the message
    ${token2} =    Token of    user2    ${USER_PASSWORD}
    Matrix    read    '${room_id}'    ${MESSAGE}    token=${token2}

*** Test Cases ***
Create the LDAP user domain
    ${response} =    Run cluster task    cluster/add-internal-provider    {"image":"openldap","node":1}
    Set Suite Variable    ${ldap_id}    ${response}[module_id]
    Run cluster task    module/${ldap_id}/configure-module    {"domain":"${USER_DOMAIN}","admuser":"admin","admpass":"${USER_PASSWORD}","provision":"new-domain"}
    Run cluster task    module/${ldap_id}/add-user    {"user":"user1","display_name":"User One","password":"${USER_PASSWORD}"}
    Run cluster task    module/${ldap_id}/add-user    {"user":"user2","display_name":"User Two","password":"${USER_PASSWORD}"}
    Put File    ${CURDIR}/matrix_client.py    /root/matrix_client.py

Add module for ${SCENARIO} scenario
    IF    r'${SCENARIO}' == 'update'
        Set Local Variable    ${install_image}    ${PREVIOUS_IMAGE_URL}
    ELSE
        Set Local Variable    ${install_image}    ${IMAGE_URL}
    END
    ${output}    ${rc} =    Execute Command    add-module ${install_image} 1
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    add-module ${install_image} failed: ${output}
    &{output} =    Evaluate    ast.literal_eval(r'''${output}''')    modules=ast
    Set Suite Variable    ${module_id}    ${output.module_id}

Configure initial module
    Configure current module
    Read admin credentials
    IF    r'${SCENARIO}' == 'update'
        # Data written before the update must survive it
        Users chat through the LDAP domain
    END

Update module from the previous release
    Skip If    r'${SCENARIO}' != 'update'    only the update scenario updates the module
    # Same request as the Software Center: no forced pull.
    ${output}    ${rc} =    Execute Command    api-cli run update-module --data '{"module_url":"${IMAGE_URL}","instances":["${module_id}"]}'
    ...    return_rc=True
    Should Be Equal As Integers    ${rc}    0    update-module ${IMAGE_URL} failed: ${output}
    ${journal} =    Execute Command    journalctl -q --no-pager SYSLOG_IDENTIFIER=agent@${module_id}
    Should Not Contain    ${journal}    has failed    an update-module.d step failed
    ${image} =    Execute Command    runagent -m ${module_id} printenv IMAGE_URL
    Should Be Equal    ${image}    ${IMAGE_URL}

Check data kept by the update
    Skip If    r'${SCENARIO}' != 'update'    only the update scenario has data from the previous release
    user2 reads the message

Configure the updated module
    Skip If    r'${SCENARIO}' != 'update'    only the update scenario updates the module
    # Releases before 0.2.0 had no UDP ports; enabling calls allocates them
    Configure current module
    ${range} =    Execute Command    runagent -m ${module_id} printenv UDP_PORTS_RANGE
    Should Match Regexp    ${range}    ^[0-9]+-[0-9]+$

Synapse answers through Traefik
    ${versions} =    Matrix    versions
    Should Not Be Empty    ${versions}[versions]
    Unit is    synapse.service    active
    Unit is    postgresql.service    active

Federation API and server keys are served
    ${keys} =    Matrix    get    /_matrix/key/v2/server
    Should Be Equal    ${keys}[body][server_name]    ${SERVER_NAME}

Server name delegation is served on the server name
    ${server} =    Matrix on    ${SERVER_NAME}    get    /.well-known/matrix/server
    Should Be Equal    ${server}[body]    ${{{"m.server": "${HOST}:443"}}}
    Should Start With    ${server}[content_type]    application/json
    ${client} =    Matrix on    ${SERVER_NAME}    get    /.well-known/matrix/client
    Should Be Equal    ${client}[body][m.homeserver][base_url]    https://${HOST}
    Unit is    wellknown.service    active

Admin account is a server admin
    Read admin credentials
    ${user} =    Matrix    server-admin    @matrixadmin:${SERVER_NAME}    token=${admin_token}
    Should Be True    ${user}[admin]

LDAP users log in and chat
    Users chat through the LDAP domain

Wrong password is rejected
    ${result} =    Matrix    bad-login    user1    wrong-password
    Should Be Equal    ${result}[errcode]    M_FORBIDDEN

Bridge bots answer
    Unit is    bridge@whatsapp.service    active
    Unit is    bridge@discord.service    active
    Bot answers    whatsappbot
    Bot answers    discordbot

TURN server answers and is announced
    Unit is    coturn.service    active
    Read admin credentials
    ${turn} =    Matrix    get    /_matrix/client/v3/voip/turnServer    token=${admin_token}
    ${uri} =    Set Variable    ${turn}[body][uris][0]
    Should Match Regexp    ${uri}    ^turn:${HOST}:[0-9]+\\?transport=udp$
    Should Not Be Empty    ${turn}[body][password]
    ${port} =    Evaluate    $uri.split(":")[2].split("?")[0]
    Matrix    stun    ${port}
    ${firewall} =    Execute Command    firewall-cmd --info-service=${module_id}
    Should Contain    ${firewall}    ${port}/udp

Element Call backend is reachable
    Unit is    livekit.service    active
    Unit is    lk-jwt.service    active
    Read admin credentials
    ${transports} =    Matrix    get    /_matrix/client/unstable/org.matrix.msc4143/rtc/transports    token=${admin_token}
    Should Be Equal    ${transports}[body][rtc_transports][0][livekit_service_url]    https://${HOST}/livekit/jwt
    ${client} =    Matrix on    ${SERVER_NAME}    get    /.well-known/matrix/client
    Should Be Equal    ${client}[body][org.matrix.msc4143.rtc_foci][0][livekit_service_url]    https://${HOST}/livekit/jwt
    ${health} =    Matrix    get    /livekit/jwt/healthz
    ${sfu} =    Matrix    get    /livekit/sfu/
    Should Be Equal    ${sfu}[body]    OK
    ${token} =    Matrix    sfu-token    token=${admin_token}
    Should Be Equal    ${token}[url]    wss://${HOST}/livekit/sfu
    Should Be Equal As Integers    ${token}[jwt_parts]    3
    ${element} =    Matrix on    ${ELEMENT_HOST}    get    /config.json
    Should Be True    ${element}[body][element_call][use_exclusively]

Web apps are served
    ${element} =    Matrix on    ${ELEMENT_HOST}    get    /config.json
    Should Be Equal    ${element}[body][default_server_config][m.homeserver][base_url]    https://${HOST}
    ${cinny} =    Matrix on    ${CINNY_HOST}    get    /config.json
    Should Be Equal    ${cinny}[body][homeserverList]    ${{["${HOST}"]}}
    ${ketesa} =    Matrix on    ${KETESA_HOST}    get    /config.json
    Should Be Equal    ${ketesa}[body][restrictBaseUrl]    https://${HOST}
    ${index} =    Matrix on    ${ELEMENT_HOST}    get    /
    Should Contain    ${index}[body]    Element

Saving unchanged settings keeps the services running
    ${synapse} =    Container id    synapse
    ${bridge} =    Container id    bridge-whatsapp
    Configure current module
    ${after} =    Container id    synapse
    Should Be Equal    ${after}    ${synapse}    Synapse was restarted although nothing changed
    ${after} =    Container id    bridge-whatsapp
    Should Be Equal    ${after}    ${bridge}    the WhatsApp bridge was restarted although nothing changed

A bridge can be disabled
    Configure current module    bridges=${{["whatsapp"]}}
    Unit is    bridge@discord.service    inactive
    Unit is    bridge@whatsapp.service    active
    ${rc} =    Execute Command    runagent -m ${module_id} test ! -e synapse/appservices/discord.yaml
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    the Discord registration is still loaded by Synapse
    &{config} =    Get configuration
    Should Be Equal    ${config.bridges}    ${{["whatsapp"]}}
    Bot answers    whatsappbot

Calls can be disabled
    Configure current module    bridges=${{["whatsapp"]}}    turn=${False}    element_call=${False}
    Unit is    coturn.service    inactive
    Unit is    livekit.service    inactive
    Unit is    lk-jwt.service    inactive
    ${rc} =    Execute Command    firewall-cmd --info-service=${module_id}
    ...    return_rc=True    return_stdout=False
    Should Not Be Equal As Integers    ${rc}    0    the firewall service of the call ports remains
    ${turn} =    Matrix    get    /_matrix/client/v3/voip/turnServer    token=${admin_token}
    Should Not Contain    ${turn}[body]    uris
    Configure current module    bridges=${{["whatsapp"]}}
    Unit is    coturn.service    active
    Unit is    livekit.service    active

Invalid settings are rejected
    ${output}    ${rc} =    Run action    configure-module    {"host": "${HOST}", "server_name": "other.test", "http2https": false, "lets_encrypt": false}
    Should Not Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    server_name_immutable
    ${output}    ${rc} =    Run action    configure-module    {"host": "${HOST}", "http2https": false, "lets_encrypt": false, "bridges": ["telegram"]}
    Should Not Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    telegram_credentials_required
    ${output}    ${rc} =    Run action    configure-module    {"host": "${HOST}", "http2https": false, "lets_encrypt": false, "element_host": "${HOST}"}
    Should Not Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    hostname_already_used
    ${output}    ${rc} =    Run action    configure-module    {"host": "${HOST}", "http2https": false, "lets_encrypt": false, "calls_public_ip": "192.168.1.10"}
    Should Not Be Equal As Integers    ${rc}    0
    Should Contain    ${output}    invalid_ip_address
    &{config} =    Get configuration
    Should Be Equal    ${config.element_host}    ${ELEMENT_HOST}

Secrets stay in private files
    ${modes} =    Execute Command    runagent -m ${module_id} stat -c '%a' matrix.json postgres.env synapse synapse/homeserver.yaml bridges/whatsapp/config.yaml calls calls/turnserver.conf calls/livekit.yaml calls/lk-jwt.env
    Should Be Equal    ${modes}    600\n600\n700\n600\n600\n700\n600\n600\n600
    ${environment} =    Execute Command    runagent -m ${module_id} cat environment
    Should Not Contain    ${environment}    ${matrix_admin_password}
    Should Not Contain    ${environment}    PASSWORD

Database dump and restore keep the data
    ${output}    ${rc} =    Execute Command    runagent -m ${module_id} module-dump-state
    ...    return_rc=True    return_stderr=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    dump failed: ${output}
    ${dump} =    Execute Command    runagent -m ${module_id} grep -c -E '^CREATE DATABASE (synapse|mautrix_whatsapp) ' postgresql.pg_dump
    Should Be Equal    ${dump}    2
    # The same steps as restore-module: new data volume from the dump, then
    # configure-module with the stored settings
    ${output}    ${rc} =    Execute Command    runagent -m ${module_id} restore-database
    ...    return_rc=True    return_stderr=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    restore failed: ${output}
    Configure current module    bridges=${{["whatsapp"]}}
    user2 reads the message
    Bot answers    whatsappbot
    ${rc} =    Execute Command    runagent -m ${module_id} module-dump-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    second dump failed
    ${rc} =    Execute Command    runagent -m ${module_id} module-cleanup-state
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
    ${rc} =    Execute Command    runagent -m ${module_id} test ! -e postgresql.pg_dump
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0    the dump remains after cleanup

Backup includes settings, bridges, dump and media
    ${include} =    Execute Command    runagent -m ${module_id} sh -c 'cat ../etc/state-include.conf'
    FOR    ${line}    IN    state/matrix.json    state/postgresql.pg_dump    state/bridges    volumes/synapse-data
        Should Match Regexp    ${include}    (?m)^${line}$
    END

Module UI loads in cluster-admin
    [Tags]    ui
    Import Library    Browser
    New Browser    chromium    headless=True
    New Context    ignoreHTTPSErrors=True
    Login to cluster-admin
    Go To    https://${NODE_ADDR}/cluster-admin/#/apps/${module_id}
    Wait For Elements State    iframe >>> h2 >> text="Status"    visible    timeout=20s
    Take Screenshot    filename=${OUTPUT DIR}/browser/screenshot/1._Status.png
    Go To    https://${NODE_ADDR}/cluster-admin/#/apps/${module_id}?page=settings
    Wait For Elements State    iframe >>> h2 >> text="Settings"    visible    timeout=20s
    # The heading renders even when module tasks fail; the fields are only
    # enabled and filled after get-configuration has completed
    Wait For Elements State    iframe >>> input#matrix-host    enabled    timeout=30s
    Get Property    iframe >>> input#matrix-host    value    ==    ${HOST}
    Get Property    iframe >>> input#server-name    value    ==    ${SERVER_NAME}
    Get Property    iframe >>> input#admin-user    value    ==    @matrixadmin:${SERVER_NAME}
    Get Property    iframe >>> input#homeserver-url    value    ==    https://${HOST}/
    Get Property    iframe >>> input#bridge-whatsapp    checked    ==    ${True}
    Get Property    iframe >>> input#bridge-discord    checked    ==    ${False}
    Take Screenshot    filename=${OUTPUT DIR}/browser/screenshot/2._Settings.png
    Close Browser

Remove module
    ${rc} =    Execute Command    remove-module --no-preserve ${module_id}
    ...    return_rc=True    return_stdout=False
    Should Be Equal As Integers    ${rc}    0
