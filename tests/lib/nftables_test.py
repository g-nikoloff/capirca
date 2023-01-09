# Copyright 2022 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unittest for Nftables rendering module."""

import datetime
from unittest import mock
from absl import logging
from absl.testing import absltest
from absl.testing import parameterized
from capirca.lib import aclgenerator
from capirca.lib import nacaddr
from capirca.lib import naming
from capirca.lib import nftables
from capirca.lib import policy


class DictObj:
  """Helper class to use a dictionary of dictionaries to form an object.

  We can then specifically test using it.
  """

  def __init__(self, in_dict: dict):
    assert isinstance(in_dict, dict)
    for key, val in in_dict.items():
      if isinstance(val, (list, tuple)):
        setattr(self, key,
                [DictObj(x) if isinstance(x, dict) else x for x in val])
      else:
        setattr(self, key, DictObj(val) if isinstance(val, dict) else val)

# "logging" is not a token.
SUPPORTED_TOKENS = frozenset({
    'action',
    'comment',
    'destination_address',
    'destination_address_exclude',
    'destination_port',
    'expiration',
    'icmp_type',
    'name',  # obj attribute, not token
    'option',
    'protocol',
    'platform',
    'platform_exclude',
    'source_address',
    'source_address_exclude',
    'source_port',
    'translated',  # obj attribute, not token
    'stateless_reply',
})

SUPPORTED_SUB_TOKENS = {
    'action': {'accept', 'deny'},
    'option': {'established', 'tcp-established'},
    'icmp_type': {
        'alternate-address',
        'certification-path-advertisement',
        'certification-path-solicitation',
        'conversion-error',
        'destination-unreachable',
        'echo-reply',
        'echo-request',
        'mobile-redirect',
        'home-agent-address-discovery-reply',
        'home-agent-address-discovery-request',
        'icmp-node-information-query',
        'icmp-node-information-response',
        'information-request',
        'inverse-neighbor-discovery-advertisement',
        'inverse-neighbor-discovery-solicitation',
        'mask-reply',
        'mask-request',
        'information-reply',
        'mobile-prefix-advertisement',
        'mobile-prefix-solicitation',
        'multicast-listener-done',
        'multicast-listener-query',
        'multicast-listener-report',
        'multicast-router-advertisement',
        'multicast-router-solicitation',
        'multicast-router-termination',
        'neighbor-advertisement',
        'neighbor-solicit',
        'packet-too-big',
        'parameter-problem',
        'redirect',
        'redirect-message',
        'router-advertisement',
        'router-renumbering',
        'router-solicit',
        'router-solicitation',
        'source-quench',
        'time-exceeded',
        'timestamp-reply',
        'timestamp-request',
        'unreachable',
        'version-2-multicast-listener-report',
    },
}

HEADER_TEMPLATE = """
header {
  target:: nftables %s
}
"""

HEAD_OVERRIDE_DEFAULT_ACTION = """
header {
  target:: nftables inet output ACCEPT
}
"""

HEADER_COMMENT = """
header {
  comment:: "Noverbose + custom priority policy example"
  target:: nftables inet output ACCEPT
}
"""

# TODO(gfm): Noverbose testing once Term handling is added.
HEADER_NOVERBOSE = """
header {
  target:: nftables mixed output noverbose
}
"""

GOOD_HEADER_1 = """
header {
  target:: nftables inet6 INPUT
}
"""

GOOD_HEADER_2 = """
header {
  target:: nftables mixed output accept
}
"""

GOOD_HEADER_3 = """
header {
  target:: nftables inet input
}
"""

ESTABLISHED_OPTION_TERM = """
term established-term {
  protocol:: udp
  option:: established
  action:: accept
}
"""

TCP_ESTABLISHED_OPTION_TERM = """
term tcp-established-term {
  protocol:: tcp
  option:: tcp-established
  action:: accept
}
"""

ICMP_TERM = """
term good-icmp {
  protocol:: icmp
  action:: accept
}
"""

ICMPV6_TERM = """
term good-icmpv6 {
  protocol:: icmpv6
  action:: accept
}
"""

GOOD_TERM_1 = """
term good-term-1 {
  action:: accept
}
"""

GOOD_TERM_2 = """
term good-term-2 {
   protocol:: tcp
   action:: accept
   destination-port:: SSH
   destination-address:: TEST_NET
}
"""

IPV6_TERM_2 = """
term inet6-icmp {
  action:: deny
}
"""

EXCLUDE = {'ip6': [nacaddr.IP('::/3'), nacaddr.IP('::/0')]}

# Print a info message when a term is set to expire in that many weeks.
# This is normally passed from command line.
EXP_INFO = 2

TEST_IPV4 = nacaddr.IP('10.2.3.4/32')
TEST_IPV6 = nacaddr.IP('2001:4860:8000::5/128')
TEST_IPS = [TEST_IPV4, TEST_IPV6]

def IPhelper(addresses):
  """Helper for string to nacaddr.IP conversion for parametized tests."""
  normalized = []
  if not addresses:
    # if empty list of addresses.
    return addresses
  else:
    for addr in addresses:
      normalized.append(nacaddr.IP(addr))
    return normalized


class NftablesTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    self.naming = mock.create_autospec(naming.Naming)
    self.dummyterm = nftables.Term('', '', '')

  @parameterized.parameters(('ip protocol tcp', ' ip protocol tcp'), ('', ''))
  def testAdd(self, statement, expected_output):
    result = nftables.Add(statement)
    self.assertEqual(result, expected_output)

  @parameterized.parameters((2, 'chain acl_name', '  chain acl_name'))
  def testTabSpacer(self, num_spaces, statement, expected_output):
    result = nftables.TabSpacer(num_spaces, statement)
    self.assertEqual(result, expected_output)

  @parameterized.parameters(
      ('ip', ['200.1.1.3/32', '9782:b30a:e5c6:1aa4:29ff:e57c:44a0:1b84'], [
          '200.1.1.3/32', '2606:4700:4700::1111'
      ], ['ip saddr 200.1.1.3/32 ip daddr 200.1.1.3/32']),
      ('ip', ['200.1.1.3/32', '200.1.1.4/32'], [
          '200.1.1.3/32', '200.1.1.4/32'
      ], [
          'ip saddr { 200.1.1.3/32, 200.1.1.4/32 } ip daddr { 200.1.1.3/32, 200.1.1.4/32 }'
      ]),
      ('ip6', ['8.8.8.8', '9782:b30a:e5c6:1aa4:29ff:e57c:44a0:1b84'], [
          '200.1.1.3/32', '2606:4700:4700::1111'
      ], [
          'ip6 saddr 9782:b30a:e5c6:1aa4:29ff:e57c:44a0:1b84/128 ip6 daddr 2606:4700:4700::1111/128'
      ]),
      ('ip6', ['2606:4700:4700::1111', '2606:4700:4700::1112'], [
          '2606:4700:4700::1111', '2606:4700:4700::1112'
      ], [
          'ip6 saddr { 2606:4700:4700::1111/128, 2606:4700:4700::1112/128 } ip6 daddr { 2606:4700:4700::1111/128, 2606:4700:4700::1112/128 }'
      ]),
  )
  def test_AddrStatement(self, af, src_addr, dst_addr, expected):
    # Necessary object format.
    src_obj = IPhelper(src_addr)
    dst_obj = IPhelper(dst_addr)
    result = self.dummyterm._AddrStatement(af, src_obj, dst_obj)
    self.assertEqual(result, expected)

  @parameterized.parameters(
      (['nd-router-advert', 'nd-neighbor-solicit', 'nd-neighbor-advert'
       ], '{ nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert }'),
      (['200.1.1.3/32'], '200.1.1.3/32'),
      (['1.1.1.1', '8.8.8.8'], '{ 1.1.1.1, 8.8.8.8 }'),
      (['tcp', 'udp', 'icmp'], '{ tcp, udp, icmp }'),
      (['80', '443'], '{ 80, 443 }'),
      ('53', '53'),
  )
  def testCreateAnonymousSet(self, input_data, expected):
    result = self.dummyterm.CreateAnonymousSet(input_data)
    self.assertEqual(result, expected)

  @parameterized.parameters(
      ([
          'ip6 saddr 2606:4700:4700::1111/128 ip6 daddr { 2001:4860:4860::8844/128, 2001:4860:4860::8888/128'
      ], ['tcp sport 80 tcp dport 80'],
       'ct state { ESTABLISHED, RELATED } log prefix "combo_cnt_log_established" counter',
       'accept', [
           'ip6 saddr 2606:4700:4700::1111/128 ip6 daddr { 2001:4860:4860::8844/128, 2001:4860:4860::8888/128 tcp sport 80 tcp dport 80 ct state { ESTABLISHED, RELATED } log prefix "combo_cnt_log_established" counter accept'
       ]),)
  def testGroupExpressions(self, address_expr, porst_proto_expr, opt, verdict,
                           expected_output):
    result = self.dummyterm.GroupExpressions(address_expr, porst_proto_expr,
                                             opt, verdict)
    self.assertEqual(result, expected_output)

  def testDuplicateTerm(self):
    pol = policy.ParsePolicy(GOOD_HEADER_1 + GOOD_TERM_1 + GOOD_TERM_1,
                             self.naming)
    with self.assertRaises(nftables.TermError):
      nftables.Nftables.__init__(
          nftables.Nftables.__new__(nftables.Nftables), pol,
          EXP_INFO)

  @parameterized.parameters(([(80, 80)], '80'), ([(1024, 65535)], '1024-65535'),
                            ([], ''))
  def testGroup(self, data, expected_output):
    """Test _Group function we use in Ports."""
    result = self.dummyterm._Group(data)
    self.assertEqual(result, expected_output)

  @parameterized.parameters(
      ('ip', ['tcp'], [], [], [], ['ip protocol tcp']),
      ('ip', ['tcp'], [(3198, 3199)], [
          (80, 80), (443, 443)
      ], [], ['tcp sport 3198-3199 tcp dport { 80, 443 }']),
      ('ip', ['tcp, udp'], [], [], [], ['ip protocol tcp, udp']),
      ('ip6', ['tcp'], [], [], [], ['meta l4proto tcp']),
      ('ip6', ['tcp'], [(3198, 3199)], [
          (80, 80), (443, 443)
      ], [], ['tcp sport 3198-3199 tcp dport { 80, 443 }']),
      ('ip6', ['tcp', 'udp'], [], [], [], ['meta l4proto { tcp, udp }']),
  )
  def testPortsAndProtocols(self, af, proto, src_p, dst_p, icmp_type, expected):
    result = self.dummyterm.PortsAndProtocols(af, proto, src_p, dst_p,
                                              icmp_type)
    self.assertEqual(result, expected)

  @parameterized.parameters(
      'chain_name input 0 inet extraneous_target_option',
      'ip6 OUTPUT 300 400'  # pylint: disable=implicit-str-concat
      'mixed input',
      'ip forwarding',
      'ip7 0 spaghetti',
      'ip6 prerouting',
      'chain_name',
      '',
  )
  def testBadHeader(self, case):
    logging.info('Testing bad header case %s.', case)
    header = HEADER_TEMPLATE % case
    pol = policy.ParsePolicy(header + GOOD_TERM_1, self.naming)
    with self.assertRaises(nftables.HeaderError):
      nftables.Nftables.__init__(
          nftables.Nftables.__new__(nftables.Nftables), pol,
          EXP_INFO)

  @parameterized.parameters((HEADER_NOVERBOSE, False), (HEADER_COMMENT, True))
  def testVerboseHeader(self, header_to_use, expected_output):
    pol = policy.ParsePolicy(header_to_use + GOOD_TERM_1, self.naming)
    data = nftables.Nftables(pol, EXP_INFO)
    for (_, _, _, _, _, _, verbose, _) in data.nftables_policies:
      result = verbose
    self.assertEqual(result, expected_output)

  def testGoodHeader(self):
    nftables.Nftables(
        policy.ParsePolicy(GOOD_HEADER_1 + GOOD_TERM_1, self.naming), EXP_INFO)
    nft = str(
        nftables.Nftables(
            policy.ParsePolicy(
                GOOD_HEADER_1 + GOOD_TERM_1 + GOOD_HEADER_2 + IPV6_TERM_2,
                self.naming), EXP_INFO))
    self.assertIn('type filter hook input', nft)

  def testStatefulFirewall(self):
    nftables.Nftables(
        policy.ParsePolicy(GOOD_HEADER_1 + GOOD_TERM_1, self.naming), EXP_INFO)
    nft = str(
        nftables.Nftables(
            policy.ParsePolicy(
                GOOD_HEADER_1 + GOOD_TERM_1 + GOOD_HEADER_2 + IPV6_TERM_2,
                self.naming), EXP_INFO))
    self.assertIn('ct state established,related accept', nft)

  def testOverridePolicyHeader(self):
    expected_output = 'accept'

    pol = policy.ParsePolicy(HEAD_OVERRIDE_DEFAULT_ACTION + GOOD_TERM_1,
                             self.naming)
    data = nftables.Nftables(pol, EXP_INFO)
    for (_, _, _, _, _, default_policy, _, _) in data.nftables_policies:
      result = default_policy
    self.assertEqual(result, expected_output)

  @parameterized.parameters((['127.0.0.1', '8.8.8.8'], {
      'ip': ['127.0.0.1/32', '8.8.8.8/32']
  }), (['0.0.0.0/8', '2001:db8::/32'], {
      'ip': ['0.0.0.0/8'],
      'ip6': ['2001:db8::/32']
  }))
  def testAddressClassifier(self, addr_to_classify, expected_output):
    result = nftables.Term._AddressClassifier(self, IPhelper(addr_to_classify))
    self.assertEqual(result, expected_output)

  @parameterized.parameters(
      ('ip6', ['multicast-listener-query'], ['mld-listener-query']),
      ('ip6', ['echo-request', 'multicast-listener-query'
              ], ['echo-request', 'mld-listener-query']),
      ('ip6', [
          'router-solicit', 'multicast-listener-done', 'router-advertisement'
      ], ['nd-router-solicit', 'mld-listener-done', 'nd-router-advert']),
      ('ip4', ['echo-request', 'echo-reply'], ['echo-request', 'echo-reply']),
  )
  def testMapICMPtypes(self, af, icmp_types, expected_output):
    result = self.dummyterm.MapICMPtypes(af, icmp_types)
    self.assertEqual(result, expected_output)

  @parameterized.parameters(
      ({
          'name': 'tcp_established',
          'option': ['tcp-established', 'established'],
          'counter': None,
          'logging': [],
          'protocol': ['tcp', 'icmp'],
          'action': ['deny'],
      }, ''),
      ({
          'name': 'dont_render_tcp_established',
          'option': ['tcp-established', 'established'],
          'counter': None,
          'logging': [],
          'protocol': ['icmp'],
          'action': ['accept'],
      }, 'ct state new'),
      ({
          'name': 'blank_option_donothing',
          'option': [],
          'counter': None,
          'logging': [],
          'protocol': ['icmp'],
          'action': ['accept'],
      }, 'ct state new'),
      ({
          'name': 'syslog',
          'option': [],
          'counter': None,
          'logging': ['syslog'],
          'protocol': ['tcp'],
          'action': ['accept'],
      }, 'ct state new log prefix "syslog"'),
      ({
          'name': 'logging_disabled',
          'option': [],
          'counter': None,
          'logging': ['disable'],
          'protocol': ['tcp'],
          'action': ['accept'],
      }, 'ct state new'),
      ({
          'name': 'combo_logging_tcp_established',
          'option': ['tcp-established'],
          'counter': None,
          'logging': ['true'],
          'protocol': ['tcp'],
          'action': ['accept'],
      }, 'ct state new log prefix "combo_logging_tcp_established"'),
      ({
          'name': 'combo_cnt_log_established',
          'option': ['tcp-established'],
          'counter': 'whatever-name-you-want',
          'logging': ['true'],
          'protocol': ['tcp'],
          'action': ['deny'],
      }, 'log prefix "combo_cnt_log_established" counter'),
  )
  def testOptionsHandler(self, term_dict, expected_output):
    term = DictObj(term_dict)
    result = self.dummyterm._OptionsHandler(term)
    self.assertEqual(result, expected_output)

  def testBuildTokens(self):
    self.naming.GetServiceByProto.side_effect = [['25'], ['26']]
    pol1 = nftables.Nftables(
        policy.ParsePolicy(GOOD_HEADER_1 + GOOD_TERM_1, self.naming), EXP_INFO)
    st, sst = pol1._BuildTokens()
    self.assertEqual(st, SUPPORTED_TOKENS)
    self.assertEqual(sst, SUPPORTED_SUB_TOKENS)

  @parameterized.parameters(
      (ESTABLISHED_OPTION_TERM,'WARNING: Term established-term is a established term and will not be rendered.'),
      (TCP_ESTABLISHED_OPTION_TERM, 'WARNING: Term tcp-established-term is a tcp-established term and will not be rendered.')
  )
  def testSkippedTerm(self, termdata, messagetxt):

    with self.assertLogs() as ctx:
      # run a policy object expected to be skipped and logged.
      nft = nftables.Nftables(
          policy.ParsePolicy(GOOD_HEADER_1 + termdata, self.naming),
          EXP_INFO)
    # self.assertEqual(len(ctx.records), 2)
    record = ctx.records[1]
    self.assertEqual(record.message, messagetxt)

  @parameterized.parameters(
      (GOOD_HEADER_1 + GOOD_TERM_2, 'inet6'),
      (GOOD_HEADER_1 + ICMPV6_TERM, 'inet6'),
      (GOOD_HEADER_2 + GOOD_TERM_2, 'mixed'),
      (GOOD_HEADER_3 + GOOD_TERM_2, 'inet'),
      (GOOD_HEADER_3 + ICMP_TERM, 'inet'),
  )
  def testRulesetGenerator(self, policy_data: str, expected_inet: str):
    self.naming.GetNetAddr.return_value = TEST_IPS
    self.naming.GetServiceByProto.return_value = ['22']

    nft = nftables.Nftables(
        policy.ParsePolicy(policy_data, self.naming), EXP_INFO)
    for header, terms in nft.policy.filters:
      filter_options = header.FilterOptions('nftables')
      nf_af, nf_hook, _, _, verbose = nft._ProcessHeader(filter_options)
      for term in terms:
        term_object = nftables.Term(term, nf_af, nf_hook, verbose)

        # Checks for address family consistency within terms
        ruleset_list = term_object.RulesetGenerator(term)
        self.assertNotEmpty(ruleset_list)
        for ruleset in ruleset_list:
          if expected_inet == 'inet':
            self.assertNotIn(str(TEST_IPV6), ruleset)
          elif expected_inet == 'inet6':
            self.assertNotIn(str(TEST_IPV4), ruleset)

          for rule in ruleset.split('\n'):
            if rule.startswith('ip '):
              self.assertNotIn('meta l4proto', rule)
              self.assertNotIn('icmpv6', rule)
            if rule.startswith('ip6 '):
              self.assertNotIn('ip protocol', rule)
              self.assertNotIn('icmp', rule)

if __name__ == '__main__':
  absltest.main()
