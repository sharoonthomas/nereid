#This file is part of Tryton & Nereid. The COPYRIGHT file at the top level of
#this repository contains the full copyright notices and license terms.
import unittest

from .test_templates import TestTemplateLoading, TestLazyRendering
from .test_helpers import TestURLfor
from .test_signals import SignalsTestCase


def suite():
    "Nereid Helpers test suite"
    test_suite = unittest.TestSuite()
    test_suite.addTests([
        unittest.TestLoader().loadTestsFromTestCase(TestTemplateLoading),
        unittest.TestLoader().loadTestsFromTestCase(TestLazyRendering),
        unittest.TestLoader().loadTestsFromTestCase(TestURLfor),
        unittest.TestLoader().loadTestsFromTestCase(SignalsTestCase),
    ])
    return test_suite
