package smtp

import "testing"

func TestPostmasterForwardPath(t *testing.T) {
	for _, token := range []string{"postmaster", "POSTMASTER", "PoStMaStEr", "<postmaster>", "<POSTMASTER>"} {
		for _, suffix := range []string{"", " NOTIFY=SUCCESS", "\tORCPT=rfc822;admin@example.org"} {
			p := parser{s: token + suffix}
			address, err := p.parseForwardPath()
			if err != nil || address != "postmaster" || p.s != suffix {
				t.Fatalf("forward path %q: address=%q suffix=%q err=%v", token+suffix, address, p.s, err)
			}
		}
	}
}

func TestPostmasterDoesNotChangeOrdinaryRecipientsOrReversePaths(t *testing.T) {
	p := parser{s: "<ordinary@example.org> NOTIFY=SUCCESS"}
	address, err := p.parseForwardPath()
	if err != nil || address != "ordinary@example.org" || p.s != " NOTIFY=SUCCESS" {
		t.Fatalf("ordinary recipient changed: %q %q %v", address, p.s, err)
	}
	for _, value := range []string{"postmaster", "<postmaster>"} {
		p := parser{s: value}
		if _, err := p.parseReversePath(); err == nil {
			t.Fatalf("bare postmaster accepted as MAIL FROM: %q", value)
		}
	}
	for _, value := range []string{"xpostmaster", "postmasterx", "<postmaster>x", "postmaster>", "<postmaster", "<postmaster@>"} {
		p := parser{s: value}
		if _, err := p.parseForwardPath(); err == nil {
			t.Fatalf("malformed recipient accepted: %q", value)
		}
	}
	for _, value := range []string{"postmaster@@example.org", "<postmaster@example.org>", "<ordinary@example.org>"} {
		original, forward := parser{s: value}, parser{s: value}
		expected, originalError := original.parsePath()
		actual, forwardError := forward.parseForwardPath()
		if expected != actual || (originalError == nil) != (forwardError == nil) || original.s != forward.s {
			t.Fatalf("ordinary validation changed for %q", value)
		}
	}
}
