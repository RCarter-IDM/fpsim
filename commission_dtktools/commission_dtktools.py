import copy
import json
import math
import numpy as np
import os
import random
import pandas as pd
from pyDOE import lhs

from simtools.ModBuilder import ModBuilder, ModFn
from dtk.utils.core.DTKConfigBuilder import DTKConfigBuilder
from simtools.SetupParser import SetupParser

import GenerateCampaignRCM

exp_name = 'Family Planning Workflow Development'
config_fn = os.path.join('inputs', 'fp_default_config.json')

SetupParser.default_block = "HPC" # Needed?

# Commonly modified calibration variables and configuration
BASE_POPULATION_SCALE_FACTOR = 0.00333333333333333  # For quick test simulations, this is set to a very low value
N_SAMPLES = 3  # the number of distinct parameter sets to run per iteration
N_REPLICATES = 1  # replicates, 1 is highly recommended.

samples = pd.DataFrame( {'PillEfficacy': np.linspace(0,1,N_SAMPLES)} )

static_params = {
    'Base_Population_Scale_Factor': BASE_POPULATION_SCALE_FACTOR,
    'Custom_Individual_Events': [
            'Should_Not_Be_Broadcasted',
            'Choose_Next_Method_Currently_Under_Age',
            'Choose_Next_Method_Currently_On_Implant',
            'Choose_Next_Method_Currently_On_Pill',
            'Choose_Next_Method_Currently_On_Withdrawal',
            'Choose_Next_Method_Currently_Pregnant',
            'Choose_Next_Method_Currently_Post_Partum',
            'Choose_Next_Method_Currently_On_None',
            'Use_Implant',
            'Use_Pill',
            'Use_Withdrawal',
            'Use_None',
        ],
        'Demographics_Filenames': [
            'demographics.json',
            'IP_Overlay.json'
        ],
        'Enable_Property_Output': 1,
        'Report_Event_Recorder': 1,
        'Report_Event_Recorder_Events': [
            'Pregnant',
            'GaveBirth',
            'Choose_Next_Method_Currently_Under_Age',
            'Choose_Next_Method_Currently_On_Implant',
            'Choose_Next_Method_Currently_On_Pill',
            'Choose_Next_Method_Currently_On_Withdrawal',
            'Choose_Next_Method_Currently_Pregnant',
            'Choose_Next_Method_Currently_Post_Partum',
            'Choose_Next_Method_Currently_On_None',
            'Use_Implant',
            'Use_Pill',
            'Use_Withdrawal',
            'Use_None',
        ],
        'Report_Event_Recorder_Ignore_Events_In_List': 0,
        'Report_Event_Recorder_Individual_Properties': [
            'CurrentStatus'
        ],
        'logLevel_RandomChoiceMatrix': 'ERROR'
}

def base_table_for_scenario(template_set_name, scenario_name, campaign_filename):
    base_table = {
        'TAGS': {'Scenario': scenario_name}
    }
    return base_table


config_builder = DTKConfigBuilder()
config_builder.ignore_missing = True

assert(os.path.isfile(config_fn))
config_builder.config = json.loads(open(config_fn).read())
config_builder.config['parameters'].update(static_params)

def constrain_sample(sample):
    '''
    if ('Pr Ex Trns Male LOW' in sample) and ('Pr Ex Trns Male MED' in sample):
        sample['Pr Ex Trns Male LOW'] = min(sample['Pr Ex Trns Male LOW'], sample['Pr Ex Trns Male MED'])
    if ('Pr Ex Trns Fem LOW' in sample) and ('Pr Ex Trns Fem MED' in sample):
        sample['Pr Ex Trns Fem LOW'] = min(sample['Pr Ex Trns Fem LOW'], sample['Pr Ex Trns Fem MED'])
    '''
    return sample


def constraint_satisfied(sample):
    return sample.equals( constrain_sample(sample) )


def map_sample_to_model_input_fn(config_builder, sample_idx, replicate_idx, sample_dict):

    config_builder.config['Run_Number'] = random.randint(0, 65535)  # Random random number seed
    print(config_builder.config)

    con_list = GenerateCampaignRCM.CreateContraceptives()
    pill_contraceptive = next(x[1] for x in con_list if x[0] == GenerateCampaignRCM.USE_PILL)
    pill_contraceptive.Waning_Config.Initial_Effect = sample_dict['PillEfficacy']

    rc_list = GenerateCampaignRCM.CreateRandomChoiceMatrixList()

    campaign = GenerateCampaignRCM.GenerateCampaignFP( con_list, rc_list )

    config_builder.campaign = campaign

    return { '[SAMPLE] %s'%k : v for k,v in sample_dict.items() }

    # For calibration use, only
    #return templates.mod_dynamic_parameters(config_builder, table) # returns tags


'''
def map_sample_to_model_input(sample_dict, sample_idx, replicate_idx, random_run_number=True):
    """
    This method is used directly by the scenario-running script. Do not change its name or argument list.
    s is a dict of user parameters that need mapping to actual model parameters
    """

    table = base_table_for_scenario(template_set_name=template_set_name, scenario_name=scenario_name,
                                    campaign_filename=campaign_filename)

    table['TAGS'].update({'__sample_index__': sample_idx, '__replicate_index__': replicate_idx})

    if random_run_number:
        table['Run_Number'] = random.randint(0, 65535)  # Random random number seed

    sample = copy.deepcopy(sample_dict)

    table['TAGS'].update({
        '[SAMPLE] %s'%k : v
        for k,v in sample.items()
    })

    table['Config_Name'] = '%s: Sample %d Rep %d'%(scenario_name, sample_idx, replicate_idx)

    if 'PillEfficacy' in sample:
        value = sample.pop('PillEfficacy')
        table['PillEfficacy'] = np.exp(value)

    for param_name,p in params.iterrows():
        if param_name in sample and 'MapTo' in p:
            if isinstance(p['MapTo'],float) and math.isnan(p['MapTo']):
                continue
            table[p['MapTo']] = sample.pop( param_name )

    # Verify all parameters were mapped
    for name, value in sample.items():
        print('UNUSED PARAMETER:', name)
    assert(len(sample) == 0)  # All params used

    return table
'''


exp_builder = ModBuilder.from_combos(
    [
        ModFn(map_sample_to_model_input_fn,
            sample[0],  # <-- sample index
            rep,        # <-- replicate index
            {k:v for k,v in zip(samples.columns.values, sample[1:])})
        for sample in samples.itertuples() for rep in range(N_REPLICATES)
    ])

run_sim_args =  {'config_builder': config_builder,
                 'exp_builder': exp_builder,
                 'exp_name': exp_name
                }

