"""chanxt - IL / RL / NEXT / FEXT / power-sum crosstalk from channel S-parameters.

Pipeline: read ``.sNp`` models -> map ports to (net, location) -> terminate
what is not analysed -> compute basic and scenario (IN/OUT) metrics -> pick
curves -> HTML plots and CSV values.
"""

__version__ = "0.1.0"
