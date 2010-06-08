from PyQt4 import QtGui, QtCore
from widgets.tablewidgets import KeywordList
from widgets.validateddialog import ValidatedDialog
import ertwrapper
from widgets.combochoice import ComboChoice


from widgets.helpedwidget import HelpedWidget
from widgets.util import resourceIcon, createSeparator, may_take_a_long_time

from initandcopy import *


class InitPanel(QtGui.QFrame):
    
    def __init__(self, parent):
        QtGui.QFrame.__init__(self, parent)
        self.setFrameShape(QtGui.QFrame.Panel)
        self.setFrameShadow(QtGui.QFrame.Raised)

        initPanelLayout = QtGui.QVBoxLayout()
        self.setLayout(initPanelLayout)

        casePanel = QtGui.QFormLayout()


        def get_case_list(ert):
            fs = ert.enkf.enkf_main_get_fs(ert.main)
            caseList = ert.enkf.enkf_fs_alloc_dirlist(fs)

            list = ert.getStringList(caseList)
            ert.freeStringList(caseList)
            return list

        self.get_case_list = get_case_list # convenience: used by several functions


        casePanel.addRow("Current case:", self.createCurrentCaseCombo())
        casePanel.addRow("Cases:", self.createCaseList())


        parametersPanelLayout = QtGui.QHBoxLayout()
        parametersPanelLayout.addWidget(ParametersAndMembers(self))

        initPanelLayout.addLayout(casePanel)
        initPanelLayout.addWidget(createSeparator())
        initPanelLayout.addLayout(parametersPanelLayout)

    def casesUpdated(self):
        """Emit to all listeners that the a new case has been added or the current case has changed"""
        self.currentCase.modelEmit("casesUpdated()")

    def createCaseList(self):
        """Creates a list that enables the creation of new cases. Removal has been disabled."""
        cases = KeywordList(self, "", "case_list")

        cases.newKeywordPopup = lambda list : ValidatedDialog(cases, "New case", "Enter name of new case:", list).showAndTell()
        cases.addRemoveWidget.enableRemoveButton(False)  #todo: add support for removal
        cases.list.setMaximumHeight(150)

        cases.initialize = lambda ert : [ert.prototype("long enkf_main_get_fs(long)"),
                                         ert.prototype("long enkf_fs_alloc_dirlist(long)"),
                                         ert.prototype("bool enkf_fs_has_dir(long)"),
                                         ert.prototype("void enkf_fs_select_write_dir(long, char*, bool)")]

        def create_case(ert, cases):
            fs = ert.enkf.enkf_main_get_fs(ert.main)

            for case in cases:
                if not ert.enkf.enkf_fs_has_dir(fs, case):
                    ert.enkf.enkf_fs_select_write_dir(fs, case, True)
                    break

            self.currentCase.updateList(self.get_case_list(ert))
            self.currentCase.fetchContent()
            self.casesUpdated()

        cases.getter = self.get_case_list
        cases.setter = create_case

        return cases


    def createCurrentCaseCombo(self):
        """Creates the combo that enables selection of the current case"""
        self.currentCase = ComboChoice(self, ["none"], help="current_case_selection")
        self.currentCase.combo.setMinimumWidth(150)

        def initialize_cases(ert):
            ert.prototype("long enkf_main_get_fs(long)")
            ert.prototype("char* enkf_fs_get_read_dir(long)")
            ert.prototype("void enkf_fs_select_read_dir(long, char*)")
            ert.prototype("void enkf_fs_select_write_dir(long, char*, bool)")

            self.currentCase.updateList(self.get_case_list(ert))

        self.currentCase.initialize = initialize_cases

        def get_current_case(ert):
            fs = ert.enkf.enkf_main_get_fs(ert.main)
            currentCase = ert.enkf.enkf_fs_get_read_dir(fs)
            #print "The selected case is: " + currentCase
            return currentCase

        self.currentCase.getter = get_current_case

        @may_take_a_long_time
        def select_case(ert, case):
            case = str(case)
            #print "Selecting case: " + case
            if not case == "":
                fs = ert.enkf.enkf_main_get_fs(ert.main)
                ert.enkf.enkf_fs_select_read_dir(fs, case)
                ert.enkf.enkf_fs_select_write_dir(fs, case, False)
                self.casesUpdated()

        self.currentCase.setter = select_case

        return self.currentCase
