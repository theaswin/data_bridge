import xmlrpc.client
def get_models_data():

    # 🔹 Hardcoded credentials
    url = "https://erp.wordcraftgroup.com"
    db = "wordcraftgroup"
    username = "9895569340"
    password = "123456"
    source_model = "res.partner"
    domain = [('active', '=', True),('id','>',100)]   # IMPORTANT: list of tuples

    print(f"Connecting to {url}...")

    # XML-RPC endpoints
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    models_proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    # Authenticate
    uid = common.authenticate(db, username, password, {})

    if not uid:
        raise Exception("Authentication failed")

    print(f"Logged in. UID: {uid}")

    print("\n===== DEBUG VALUES =====")
    print("DB:", db, type(db))
    print("UID:", uid, type(uid))
    print("Password:", password, type(password))
    print("Model:", source_model, type(source_model))
    print("Domain:", domain, type(domain))
    print("=========================\n")

    # Execute search_count
    record_count = models_proxy.execute_kw(
        db,
        uid,
        password,
        source_model,
        'search_count',
        [domain]
    )

    print("Record Count:", record_count)

get_models_data()
