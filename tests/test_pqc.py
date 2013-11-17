# I should split in two tests, one for generic expected proprieties and
#   contents, and another test for specific contents, like keys, and values
#   itself. But this last one must require a md5.

import numpy as np

def func():
    from seabird import cnv
    import cotede.qc
    data = cnv.fCNV('./tests/dPIRX010.cnv')
    pqc = cotede.qc.ProfileQC(data, saveauxiliary=True)
    return pqc


def test_answer():
    pqc = func()
    keys = ['timeS', 'pressure', 'temperature', 'temperature2', 'conductivity',
            'conductivity2', 'potemperature', 'potemperature2', 'salinity',
            'salinity2', 'flag']
    pqc.keys() == keys
    assert type(pqc.attributes) == dict
    assert len(pqc.attributes) == 11
    assert hasattr(pqc, 'input')
    assert hasattr(pqc, 'flags')
    assert hasattr(pqc, 'auxiliary')
    assert type(pqc.flags) == dict
    for k in pqc.flags.keys():
        assert type(pqc.flags[k]) == dict
        for kk in pqc.flags[k].keys():
            assert (type(pqc.flags[k][kk]) == np.ndarray) or \
                (type(pqc.flags[k][kk]) == bool)
            if (type(pqc.flags[k][kk]) == np.ndarray):
                assert pqc.flags[k][kk].dtype == 'int8'
