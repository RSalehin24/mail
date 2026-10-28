# Supplemental strict identity check after Maddy's RFC address parser.
# Maddy 0.9.6 authorize_sender accepts either From or Sender. Require both
# (when Sender is present) to identify the authenticated mailbox.
function identity(value, address) {
    sub(/^[ \t]+/, "", value)
    sub(/[ \t]+$/, "", value)
    address = value
    if (value ~ /</) {
        sub(/^.*</, "", address)
        if (address !~ />[ \t]*$/) return 0
        sub(/>[ \t]*$/, "", address)
    }
    return tolower(address) == tolower(expected)
}
function field() {
    if (name == "from" || name == "sender") {
        count[name]++
        if (count[name] > 1 || !identity(value)) invalid = 1
    }
}
BEGIN { headers = 1 }
{
    sub(/\r$/, "")
    if (!headers) next
    if ($0 == "") { field(); name = ""; headers = 0; next }
    if ($0 ~ /^[ \t]/) { value = value " " $0; next }
    field()
    colon = index($0, ":")
    name = tolower(substr($0, 1, colon - 1))
    value = substr($0, colon + 1)
}
END {
    if (headers) field()
    if (invalid || count["from"] != 1) exit 1
}
