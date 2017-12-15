import time, numpy, httplib2, os, threading, signal, sys, socket
from datetime import datetime
import RPi.GPIO as GPIO
import gspread
import random
import numpy as np
from oauth2client.service_account import ServiceAccountCredentials

# Variable used for testing.  This determines how much output the program
# will have during running.
# 1 = All output
# 2 = Some output
# 5 = Over nigth Debug - Only basic output to save space
TESTING = 5
# Throw out distance if over this value
MAX_DIS = 160
# The month the new fiscal year starts, July = 7
FISCAL_START = 7
# The variance in the distance when checking
DISTANCE_VARIANCE = 0.80
LAST_DIS_VAR = 0.80
# Total count of the people for that hour
TOTAL_COUNT = 0
# Variables used for the ultrasonic sensor
TRIG = 23
ECHO = 24
HOUR = -1
# Total numbers to gather initial distance to wall or floor
GAUGE_COUNT = 20
# The settle timer for the ultrasonic sensor
SETTLETIMER = 0.1
# The amount of time to sleep between pings of the ultrasonic sensor
SLEEPTIMER = 0.05
# Timer for gaguing distances
GAGUETIMER = 0.1
# Timer for submissions to happen
CONTROLLER_SLEEP = 30
# The last distance that was grabbed by the sensor
LAST_DISTANCE = 0
LAST_AVG_QP = 0
# The average of the initial distance
AVG_DISTANCE = 0
# Used to make sure the pins are only set once
PINS_SET = 0
# Variable to gauge distance over multiple pings without waiting between
NO_WAIT_PING = 50
QUICK_WAIT = 0.05
# Used for checking if this is a door opening or closing
DROP_DISTANCE = .93
GAIN_DISTANCE = 1.07
# Variables used to determine when the system will restart itself
REBOOT_HOUR = 0
REBOOT_MIN = 2

###################################################################
# This is used to controll the thread responsible for the sensor
# readings.
###################################################################
def sensorController( countingLock, measureLock,f, *args ):
    try:
        if TESTING == 1:
            print( "Sensor Started" )
        sensor( countingLock, measureLock, f )# Starts the sensor function
    except KeyboardInterrupt:
        GPIO.cleanup()
        sys.exit(0)


###################################################################
# This is used to control the thread responsible for submitting the
# data to the google sheet.
###################################################################
def submitController( countingLock, measureLock, f, *args ):
    global REBOOT_HOUR
    global REBOOT_MIN
    try:
        if TESTING == 1:
            print( "Submit Started")
        current_hour = get_hour()# Grab the current hour
        while True:
            # Checks to see if current hour matches the hour it just checked
            if current_hour != get_hour():
                f.write( current_date() + "- It's a new hour.\n" )
                current_hour = get_hour()# reset current hour to the new hour
                post_count( countingLock, f )# submit count for the hour

            now = datetime.now()# Grabs current time
            if now.hour == REBOOT_HOUR and now.minute == REBOOT_MIN:
                reboot(f)# Reboots the system
            time.sleep( CONTROLLER_SLEEP )# sleep before checking again
    except KeyboardInterrupt:
        GPIO.cleanup()
        sys.exit(0)

###################################################################
# Method used to get quick pings of distance without waiting in between
# each ping.  This function will return a distance that is the average
# of the array of distances.
###################################################################
def quickPing( measureLock ):
    global NO_WAIT_PING
    global QUICK_WAIT
    global TESTING

    avgDist = []# Set up array to store  distances

    # Pings multiple times without waiting to get an array of values
    # these values will then be averaged out and checked against then
    # other distances
    for i in range(0, NO_WAIT_PING):
        time.sleep(QUICK_WAIT)
        measureLock.acquire()# lock sensor
        d2 = get_Ping()# Get new pings
        measureLock.release()# Release sensor
        if( TESTING == 1 ):
            print( "QP: ", d2 )
        avgDist.append(d2)# Add this to the array of distances

    if( TESTING == 1 or TESTING == 2 or TESTING == 5 ):
        print( "Time: " + current_date() )
        print( "*** Avg QP Distance is:", numpy.average( avgDist ) )
    # Checks to see if a door or person
    if( doorCheck( avgDist ) == 1 ):
        return numpy.average( avgDist )
    else:
        return 0

###################################################################
# Method used to check if the counts are from a door opening or closing
# uses a error range to check againts the previous value.
#
# Returns if the counts look to be the door opeing or closing.
###################################################################
def doorCheck( distArr ):
    global GAIN_DISTANCE
    global DROP_DISTANCE
    global TESTING

    previ = distArr[0]
    dr = []

    for d in distArr:
        if d*DROP_DISTANCE > previ  or d*GAIN_DISTANCE < previ:
            dr.append(1)
        else:
            dr.append(0)
        previ = d

    t = np.sum(dr)# Sums all true counts from the distance array
    if TESTING == 1 or TESTING == 2 or TESTING == 5:
        print( "Time: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') )
        print( "Count of true for door check: ", t )
        print( "Total number of counts: ", len(distArr) )
    # Checks to see if over half answers are true then considers count a door
    if( (len(distArr) * 0.4) < t ):
        return 0# Ignore thinking its a door
    else:
        return 1

###################################################################
# Checks to make sure the distance can be counted.  If there is a
# distance that is less than a certain percentage of the initial
# distance then it checks to see if it is close to the last distance
# measured if so then it is not counted but if it is not then it adds
# returns true.
#
# 1 = count
# 0 = do not count
###################################################################
def check_distance( distance, measureLock ):
    global LAST_DISTANCE
    global AVG_DISTANCE
    global DISTANCE_VARIANCE
    global LAST_DIS_VAR
    global LAST_AVG_QP
    global NO_WAIT_PING
    global TESTING

    # Make sure that the distance is not greater than MAX_DIS
    if distance > MAX_DIS:
        return 0
    # Make sure distance is greater than 0
    if distance <= 0:
        return 0

    # Ping distance is less than the average distance multipled by an error percent
    if distance <= (AVG_DISTANCE * DISTANCE_VARIANCE):
        d2 = quickPing( measureLock )# Get average of a few quick pings to check validity
        # Checks to see if avg dist is 0, means there was an error or no count
        if( d2 == 0 ):
            return 0# Ignore count

        # Check to see if the last quick ping isnt similar to this quick ping.
        if( ((d2*1.05)<= LAST_AVG_QP) and ((d2*.95)>=LAST_AVG_QP) ):
            return 0# Ignore count

        if TESTING == 1 or TESTING == 2:
            print( "First distance is: ", distance )
            print( "Avg distance for ", NO_WAIT_PING, " is: ", d2 )

        if d2 >= (distance * 1.3):
            LAST_DISTANCE = distance# Reset LAST_DISTANCE to new distance
            LAST_AVG_QP = d2
            return 0# Ignore count
        # Ping Distance is less than or equal to the last distance multipled by an error
        if d2 <= (LAST_DISTANCE * LAST_DIS_VAR):
            LAST_DISTANCE = distance# Reset LAST_DISTANCE to new distance
            LAST_AVG_QP = d2
            return 1
        # Ping Distance is greater than or equal to the last distance multipled by an error
        elif d2 >= (LAST_DISTANCE + (LAST_DISTANCE*(1-LAST_DIS_VAR)) ):
            LAST_DISTANCE = distance# Reset LAST_DISTANCE to new distance
            LAST_AVG_QP = d2
            return 1
        # Ignore distance and return 0 so it is not counted
        else:
            LAST_DISTANCE = distance# Reset LAST_DISTANCE to new distance
            LAST_AVG_QP = d2
            return 0# Ignore count
    # Ignore distance and return 0 so it is not counted
    else:
        LAST_DISTANCE = distance# Reset LAST_DISTANCE to new distance
        LAST_AVG_QP = 0
        return 0# Ignore count

###################################################################
# Uses to ultrasonic sensor to measure the distance from the sensor
# to the wall or floor.  Then uses the average that was determined to
# gague if there was someone in the doorway and then adding a count
# for this person.
###################################################################
def sensor( countingLock, measureLock, f ):
    global TOTAL_COUNT
    global AVG_DISTANCE
    global TRIG
    global ECHO
    global TESTING

    # Get the initial distance for judging other distances against
    reset_Sensor_Distance( measureLock, f )

    # Runs forever since we want this program to keep pinging while it is
    # alive.
    while True:
        try:
            if( TESTING == 1 ):
                f.write( current_date() + "- Sensor acquired measureing lock.\n" )
            measureLock.acquire()# Acquire the measure lock
            distance = get_Ping()# Measure distance
            measureLock.release()# Release the measure lock
            if( TESTING == 1 ):
                f.write( current_date() + "- Sensor released measureing lock.\n" )
                print( "Measure: ", distance )
            # Checks to see if the distance is a valid distance
            # if so then it adds to it.
            if check_distance( distance, measureLock ) == 1:
                if( TESTING == 1 or TESTING == 2 or TESTING == 5 ):
                    f.write( current_date() + "- Sensor acquired counting lock.\n" )
                countingLock.acquire()# Acquire the counter lock
                TOTAL_COUNT = TOTAL_COUNT + 1# Add 1 to count
                countingLock.release()# Release the counter lock
                if( TESTING == 1 or TESTING == 2 or TESTING == 5 ):
                    f.write( current_date() + "- Sensor released counting lock.\n" )
                    f.write( "******************************************************\n" )
                    f.write( "Time: " + current_date() + "\n" )
                    f.write( "Total Count: ", TOTAL_COUNT + "\n" )
                    f.write( "******************************************************\n" )
                    print( "******************************************************" )
                    print( "Time: " + current_date() )
                    print( "Total Count: ", TOTAL_COUNT )
                    print( "******************************************************" )

            time.sleep( SLEEPTIMER )
        except KeyboardInterrupt:
            GPIO.cleanup()
            sys.exit()


###################################################################
# Method used to fire the ultrasonic sensor and return the distance
# that was determined by the sensor.
###################################################################
def get_Ping():
    global AVG_DISTANCE
    global TRIG
    global ECHO
    global PINS_SET

    # Time when the pulse is sent out
    pulse_start = 0
    # Time when the pulse is recieved back at sensor
    pulise_end = 0

    if PINS_SET == 0:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TRIG,GPIO.OUT)
        GPIO.setup(ECHO,GPIO.IN)
        GPIO.output(TRIG, False)
        PINS_SET = 1

    GPIO.output( TRIG, True )
    time.sleep( SETTLETIMER )
    GPIO.output( TRIG, False )

    # wait for echo pin to clear
    while GPIO.input( ECHO ) == 0:
        pulse_start = time.time()

    # wait for echo pin to recieve something
    while GPIO.input( ECHO ) == 1:
        pulse_end = time.time()

    # Find the amount of time between the two times
    pulse_duration = pulse_end - pulse_start
    distance = pulse_duration * 17150# Multiply time by 34500/2 => 17150
    distance = round(distance, 5)# Round the distance to five decimal places

    return distance

###################################################################
# Method used to check the initial distance from the sensor to the
# wall/floor.  This is also run ever hour to make sure that nothing
# has changed.
###################################################################
def reset_Sensor_Distance( measureLock, f ):
    global GAUGE_COUNT
    global AVG_DISTANCE
    global TESTING

    if TESTING == 1:
        print( "Waiting For Sensor To Settle" )

    time.sleep( 2 )
    dist = []
    # Grab a number of pings
    for num in range( 0, GAUGE_COUNT ):
        if( TESTING == 1 ):
            f.write( current_date() + "- Reset Sensor Distance acquired measureing lock.\n" )
        measureLock.acquire()# Acquire measure lock
        distance = get_Ping()# Get distance
        measureLock.release()# Release measure lock
        if( TESTING == 1 ):
            f.write( current_date() + "- Reset Sensor Distance released measureing lock.\n" )

        # ignore values that are larger than the max distance accepted
        while distance > MAX_DIS:
            distance = get_Ping()
        if TESTING == 1:
            print( "Gauge Distance: ", distance )
        dist.append( distance )# Add new values to array
        time.sleep( GAGUETIMER )# Sleep

    # Find average of the array of distances
    AVG_DISTANCE = numpy.average( dist )

    if TESTING == 1 or TESTING == 5:
        print( "Time: " + current_date() )
        print( "Average Distance: ",AVG_DISTANCE )

###################################################################
# Method used for submitting the count to the google sheet.  It
# is called every hour and calls all the other methods to get the
# information needed to submit to the google sheet.
# GateCounter-9b3fa781390f.json
###################################################################
def post_count( countingLock, f ):
    global TOTAL_COUNT
    global TESTING

    # use creds to create a client to interact with the Google ()Drive API
    scope = ['https://spreadsheets.google.com/feeds']
    creds = ServiceAccountCredentials.from_json_keyfile_name('******',scope)
    client = gspread.authorize(creds)

    # Find a workbook by name and open the first sheet
    # Make sure you use the right name here.
    sheet = client.open( "Hill MakerSpace Door Count" ).sheet1
    f.write( current_date() + "- Counting lock was acquired in Post Count.\n" )
    countingLock.acquire()# Acquire lock for counting
    people = rounding( TOTAL_COUNT )# Round the count for people
    TOTAL_COUNT = 0# Reset the total count to 0 for the next hour
    countingLock.release()# Release the lock for counting
    row = [current_date(), get_count_hour(), people, get_month(), day_of_week(), fiscal_year(), get_ip_address()]
    f.write( current_date() + "- Counting lock was released in Post Count.\n" )
    sheet.append_row(row)# Add the informtation to the sheet

    if TESTING == 1 or TESTING == 2 or TESTING == 5:
        wk = client.open( "Hill MakerSpace Door Count" ).worksheet("RAW_DATA")
        wk.append_row( row )
        f.write( "************************ SUBMITTING ************************\n" )
        f.write( "Time: " + current_date() + "\n")
        f.write( "Submitted for " + get_hour() + " and count was " + people + "\n" )
        f.write( "************************************************************\n" )
        print( "************ SUBMITTING ************")
        print( "Time: " + current_date() )
        print( "Submitted for " + get_hour() + " and count was " + people )

###################################################################
###################################################################
# Returns the current date and time
###################################################################
def current_date():
    return str( datetime.now() )[:16]

###################################################################
# Returns the current month of the year.
###################################################################
def get_month():
    return datetime.now().month

###################################################################
# Function used to determine the fiscal year that we are currently
# in.  Uses the date for the current month to determine if it falls
# before of after the start of the fiscal year.  If it falls before
# the fiscal year then it is modified accordingly.
###################################################################
def fiscal_year():
    now = datetime.now()
    if now.month >= FISCAL_START:
        return now.year + 1
    else:
        return now.year

###################################################################
# Function used to determine the current day of the week.
# (Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday)
# (0     , 1      , 2        , 3       , 4     , 5       , 6     )
# The above is the day corresponding to the number it prints out
###################################################################
def day_of_week():
    day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    now = datetime.now()
    return day[ now.weekday() ]

###################################################################
# Method used to get the current hour.
###################################################################
def get_hour():
    now = datetime.now()
    return now.hour

###################################################################
# Method used to get the hour that the count is actually for. So
# takes the current hour and subtracts by 1 since the count is for
# the previous hour.
###################################################################
def get_count_hour():
    now = datetime.now()
    if( now.hour == 0 ):
        hour = 23
    else:
        hour = now.hour - 1
    return hour

###################################################################
# Method used for getting the current IP Address of the Pi so it
# can be reported to the google sheet in case someone needs to
# access the Pi through SSH or VNC.  If using VNC you must start
# tight vnc on the Pi. To do this SSH into the pi and run
# 'tightvncserver'
###################################################################
def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect( ("8.8.8.8", 80) )
    return s.getsockname()[0]

###################################################################
# Rounds the total number of counts up if it determines that the
# count is an odd number.  This is due to the fact that the direction
# the person is going through the door cannot be determined using the
# ultrasonic sensor.  Also, need to divide the total count by 2 due to
# this.
###################################################################
def rounding( count ):
    # Checks to see if total count is odd
    if count % 2 == 1:
        # Add 1 to total and divide by two
        return ( count+ 1 ) / 2
    else:
        # Divide count by 2
        return count / 2

###################################################################
# Function that should be called every day at midnight to reboot
# the device that this is runnig on.  This ensures that it is reset
# everyday.
###################################################################
def reboot(f):
    if( TESTING == 1 or TESTING == 2 ):
        f.write( "Rebooting at: " + current_date() + "\n" )
    os.system("sudo reboot")# Command to reboot linux machine

###################################################################
# Main starting point for the program
###################################################################
if __name__ == "__main__":
    try:
        f = open( "logs/log_" + str( datetime.now() )[:10] + "_.txt", "w" )
        countingLock = threading.Lock()# Lock for counting so that two arent accessing the variable at once
        measureLock = threading.Lock()# Lock for the sensor reads
        sensorThread = threading.Thread(name='SensorThread', target=sensorController, args=(countingLock,measureLock,f,))
        submitThread = threading.Thread(name='SubmitThread', target=submitController, args=(countingLock,measureLock,f,))
        sensorThread.deamon = True
        submitThread.deasmon = True
        sensorThread.start()
        submitThread.start()
    except KeyboardInterrupt:
        GPIO.cleanup()
        sys.exit(0)
